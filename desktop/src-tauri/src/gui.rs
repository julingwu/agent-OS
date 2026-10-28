//! 本地桌面操控：启动应用、枚举/切换窗口、键入文字、按组合键、点击。
//!
//! 设计约束（都是被真实故障逼出来的）：
//!   * 必须走 Win32 原生接口 —— 用 `cmd start` 起 GUI 程序不会出现在用户桌面会话，
//!     用 ShellExecuteW 才会落到当前交互式桌面。
//!   * 每次动作都要有**可验证的回执**：启动后回窗口数/前台窗口，让模型能自己确认结果，
//!     而不是"发出去就算成功"。
//!   * 找不到应用名时先查开始菜单和桌面的快捷方式，别让模型瞎猜路径。

use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HWND, LPARAM, MAX_PATH, RECT, WPARAM};
use windows::Win32::System::Threading::{
    AttachThreadInput, GetCurrentThreadId, OpenProcess, QueryFullProcessImageNameW,
    PROCESS_NAME_WIN32, PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    keybd_event, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBDINPUT,
    KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSEINPUT, MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP, MOUSE_EVENT_FLAGS, VIRTUAL_KEY, VK_MENU,
};
// 截图用的 GDI：BitBlt 抓屏 + GetDIBits 取像素（windows 0.58 里这些都在 Graphics::Gdi 下）
use windows::Win32::Graphics::Gdi::{
    BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC, GetDIBits,
    ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, CAPTUREBLT, DIB_RGB_COLORS,
    HBITMAP, HDC, HGDIOBJ, ROP_CODE, SRCCOPY,
};
use windows::Win32::UI::Shell::ShellExecuteW;
use windows::Win32::UI::WindowsAndMessaging::{
    AllowSetForegroundWindow, BringWindowToTop, EnumWindows, GetForegroundWindow,
    GetSystemMetrics, GetWindowRect, GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId,
    IsWindowVisible, PostMessageW, SetForegroundWindow, ShowWindow, SwitchToThisWindow,
    HWND_NOTOPMOST, HWND_TOPMOST, SM_CXSCREEN, SM_CYSCREEN, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW,
    SW_RESTORE, SW_SHOWNORMAL, WM_CLOSE,
};
use windows::Win32::UI::WindowsAndMessaging::{SetWindowPos, ASFW_ANY};

pub fn handle(tool: &str, args: &Value) -> Option<(bool, Value)> {
    match tool {
        "local_apps" => Some(local_apps(
            args.get("query").and_then(|v| v.as_str()).unwrap_or(""),
            args.get("limit").and_then(|v| v.as_u64()).unwrap_or(15),
        )),
        "local_open" => {
            let target = args.get("target").and_then(|v| v.as_str()).unwrap_or("");
            Some(local_open(target))
        }
        "local_windows" => Some((true, local_windows())),
        "local_focus" => {
            let title = args.get("title").and_then(|v| v.as_str()).unwrap_or("");
            Some(local_focus(title))
        }
        "local_type" => {
            let text = args.get("text").and_then(|v| v.as_str()).unwrap_or("");
            let title = args.get("title").and_then(|v| v.as_str());
            Some(local_type(text, title))
        }
        "local_keys" => {
            let keys = args.get("keys").and_then(|v| v.as_str()).unwrap_or("");
            let title = args.get("title").and_then(|v| v.as_str());
            Some(local_keys(keys, title))
        }
        "local_click" => {
            let x = args.get("x").and_then(|v| v.as_i64()).unwrap_or(-1);
            let y = args.get("y").and_then(|v| v.as_i64()).unwrap_or(-1);
            let button = args.get("button").and_then(|v| v.as_str()).unwrap_or("left");
            Some(local_click(x as i32, y as i32, button))
        }
        "local_close" => {
            let title = args.get("title").and_then(|v| v.as_str()).unwrap_or("");
            Some(close_window(title))
        }
        "local_screenshot" => Some(local_screenshot(args)),
        _ => None,
    }
}

// ---------------------------------------------------------------- 应用发现
fn start_menu_dirs() -> Vec<PathBuf> {
    let env = |k: &str| std::env::var(k).unwrap_or_default();
    let mut dirs = Vec::new();
    for base in [env("ProgramData"), env("APPDATA")] {
        if !base.is_empty() {
            dirs.push(Path::new(&base).join("Microsoft/Windows/Start Menu/Programs"));
        }
    }
    let home = env("USERPROFILE");
    if !home.is_empty() {
        dirs.push(Path::new(&home).join("Desktop"));
        dirs.push(Path::new(&home).join("OneDrive/Desktop"));
    }
    if !env("PUBLIC").is_empty() {
        dirs.push(Path::new(&env("PUBLIC")).join("Desktop"));
    }
    dirs.into_iter().filter(|d| d.is_dir()).collect()
}

fn walk_shortcuts(dir: &Path, depth: u8, out: &mut Vec<(String, PathBuf)>) {
    if depth == 0 || out.len() > 800 {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            walk_shortcuts(&path, depth - 1, out);
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let lower = name.to_lowercase();
        if lower.ends_with(".lnk") || lower.ends_with(".exe") || lower.ends_with(".url") {
            // 去掉扩展名当显示名（"Notepad.lnk" → "Notepad"）
            let stem = path
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or(name);
            out.push((stem, path));
        }
    }
}


/// 中文/口语名 → 快捷方式文件名或可执行文件名。
/// Windows 的开始菜单快捷方式是英文文件名（Notepad.lnk），用户说的是"记事本"，
/// 不做这层映射，模型查一次查不到就只能瞎猜路径。
fn app_aliases(query: &str) -> Vec<&'static str> {
    const TABLE: &[(&[&str], &[&str])] = &[
        (&["记事本", "文本编辑"], &["notepad"]),
        (&["计算器"], &["calc", "calculator"]),
        (&["画图", "绘图", "图像编辑"], &["mspaint", "paint"]),
        (&["写字板"], &["wordpad", "write"]),
        (&["命令提示符", "cmd", "命令行"], &["cmd", "command prompt"]),
        (&["终端", "terminal", "powershell"], &["windowsterminal", "wt", "powershell"]),
        (&["任务管理器"], &["taskmgr"]),
        (&["资源管理器", "文件管理", "我的电脑", "此电脑"], &["explorer", "file explorer"]),
        (&["控制面板"], &["control panel", "control"]),
        (&["设置", "系统设置"], &["settings"]),
        (&["浏览器", "谷歌浏览器"], &["chrome", "edge", "firefox"]),
        (&["微信"], &["wechat", "weixin"]),
        (&["企业微信"], &["wxwork", "wecom"]),
        (&["飞书"], &["feishu", "lark"]),
        (&["钉钉"], &["dingtalk"]),
        (&["qq"], &["qq", "tim"]),
        (&["截图", "截屏"], &["snipping", "snip"]),
        (&["计算", "数学"], &["calc"]),
        (&["日历"], &["calendar"]),
        (&["相机"], &["camera"]),
        (&["录音机"], &["soundrecorder", "recorder"]),
        (&["时钟", "闹钟"], &["clock", "alarms"]),
        (&["音乐播放器", "媒体播放器"], &["media player"]),
        (&["邮件", "outlook"], &["outlook", "mail"]),
        (&["word"], &["winword", "word"]),
        (&["excel"], &["excel"]),
        (&["ppt", "powerpoint", "演示"], &["powerpnt", "powerpoint"]),
        (&["vscode", "代码编辑器"], &["code"]),
    ];
    let q = query.trim().to_lowercase();
    let mut out = Vec::new();
    for (names, targets) in TABLE {
        if names.iter().any(|n| q == *n || q.contains(n) || n.contains(&q.as_str())) {
            out.extend(targets.iter().copied());
        }
    }
    out
}

fn find_apps(query: &str, limit: usize) -> Vec<Value> {
    let mut found: Vec<(String, PathBuf)> = Vec::new();
    for dir in start_menu_dirs() {
        walk_shortcuts(&dir, 4, &mut found);
    }
    let needle = query.trim().to_lowercase();
    let aliases = app_aliases(query);
    let hit = |name: &str, path: &Path, needle: &str, aliases: &[&str]| -> bool {
        if needle.is_empty() {
            return true;
        }
        let low_name = name.to_lowercase();
        let low_path = path.to_string_lossy().to_lowercase();
        if low_name.contains(needle) || low_path.contains(needle) {
            return true;
        }
        aliases.iter().any(|a| low_name.contains(a) || low_path.contains(a))
    };
    found
        .into_iter()
        .filter(|(name, path)| hit(name, path, &needle, &aliases))
        .take(limit.max(1))
        .map(|(name, path)| {
            json!({
                "name": name,
                "path": path.display().to_string(),
                "source": if path.to_string_lossy().to_lowercase().contains("start menu") {
                    "start_menu"
                } else { "desktop" },
            })
        })
        .collect()
}

fn local_apps(query: &str, limit: u64) -> (bool, Value) {
    let items = find_apps(query, limit as usize);
    let total = items.len();
    (
        true,
        json!({
            "query": query,
            "count": total,
            "items": items,
            "next": if items.is_empty() {
                "没匹配到快捷方式。可以直接用 local_open 传口语名（如 记事本、计算器），系统会解析；也可以用 local_windows 看已运行的应用"
            } else {
                "用 local_open 传 path 或 name 启动；启动后用 local_windows 确认窗口出现"
            },
        }),
    )
}

// ---------------------------------------------------------------- 启动
fn local_open(target: &str) -> (bool, Value) {
    if target.trim().is_empty() {
        return (false, json!({"error": "target 不能为空"}));
    }
    // 纯名字（不含路径分隔符、不带扩展名）→ 先去开始菜单里找
    let looks_like_name = !target.contains('\\')
        && !target.contains('/')
        && !target.contains(':')
        && !target.to_lowercase().ends_with(".exe")
        && !target.to_lowercase().ends_with(".lnk");

    let mut resolved = target.to_string();
    if looks_like_name {
        let hits = find_apps(target, 3);
        if let Some(first) = hits.first() {
            if let Some(path) = first.get("path").and_then(|v| v.as_str()) {
                resolved = path.to_string();
            }
        }
    }

    let op: Vec<u16> = "open\0".encode_utf16().collect();
    let file: Vec<u16> = format!("{resolved}\0").encode_utf16().collect();
    let result = unsafe {
        ShellExecuteW(
            None,
            PCWSTR(op.as_ptr()),
            PCWSTR(file.as_ptr()),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    let code = result.0 as isize;
    if code <= 32 {
        return (
            false,
            json!({
                "error": format!("启动失败（ShellExecute 返回 {code}）"),
                "target": target,
                "hint": "先 local_apps 查可用应用名/路径；code=2 表示文件没找到，code=5 表示被拒绝",
            }),
        );
    }

    // 等目标应用自己的窗口出现（最多 4 秒），别只睡死 1.2 秒就回报"前台是别的程序"
    let stem = Path::new(&resolved)
        .file_stem()
        .map(|s| s.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let mut windows_seen = Vec::new();
    let mut appeared = None;
    for _ in 0..16 {
        std::thread::sleep(std::time::Duration::from_millis(250));
        windows_seen = collect_windows();
        appeared = windows_seen.iter().find(|w| {
            let p = w.get("process").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
            !stem.is_empty() && (p.contains(&stem) || stem.contains(&p.replace(".exe", "")))
        }).cloned();
        if appeared.is_some() {
            break;
        }
    }
    let fg = windows_seen
        .iter()
        .find(|w| w.get("foreground").and_then(|v| v.as_bool()).unwrap_or(false))
        .cloned();
    (true, json!({
        "launched": true,
        "target": target,
        "resolved": resolved,
        "target_window": appeared,
        "visible_windows": windows_seen.len(),
        "foreground": fg,
        "next": "用 local_type 带上 title（如 记事本）即可先聚焦再输入；要关闭用 local_close",
    }))
}

// ---------------------------------------------------------------- 窗口
fn window_title(hwnd: HWND) -> String {
    let len = unsafe { GetWindowTextLengthW(hwnd) };
    if len <= 0 {
        return String::new();
    }
    let mut buf = vec![0u16; (len + 2) as usize];
    let copied = unsafe { GetWindowTextW(hwnd, &mut buf) };
    String::from_utf16_lossy(&buf[..copied.max(0) as usize])
}

fn process_name(pid: u32) -> String {
    unsafe {
        let Ok(handle) = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) else {
            return String::new();
        };
        let mut buf = [0u16; MAX_PATH as usize];
        let mut size = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            windows::core::PWSTR(buf.as_mut_ptr()),
            &mut size,
        )
        .is_ok();
        let _ = CloseHandle(handle);
        if !ok {
            return String::new();
        }
        let full = String::from_utf16_lossy(&buf[..size as usize]);
        full.rsplit('\\').next().unwrap_or("").to_string()
    }
}

fn collect_windows() -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    unsafe {
        let _ = EnumWindows(Some(enum_cb), LPARAM(&mut out as *mut Vec<Value> as isize));
    }
    out
}

unsafe extern "system" fn enum_cb(hwnd: HWND, lparam: LPARAM) -> windows::Win32::Foundation::BOOL {
    let out = &mut *(lparam.0 as *mut Vec<Value>);
    if out.len() >= 80 {
        return windows::Win32::Foundation::BOOL(0);
    }
    if !IsWindowVisible(hwnd).as_bool() {
        return windows::Win32::Foundation::BOOL(1);
    }
    let title = window_title(hwnd);
    if title.trim().is_empty() {
        return windows::Win32::Foundation::BOOL(1);
    }
    let mut pid = 0u32;
    GetWindowThreadProcessId(hwnd, Some(&mut pid));
    let fg = GetForegroundWindow();
    out.push(json!({
        "title": title,
        "pid": pid,
        "process": process_name(pid),
        "hwnd": hwnd.0 as isize,
        "foreground": hwnd == fg,
    }));
    windows::Win32::Foundation::BOOL(1)
}

fn local_windows() -> Value {
    let mut wins = collect_windows();
    let count = wins.len();
    wins.sort_by_key(|w| {
        !w.get("foreground").and_then(|v| v.as_bool()).unwrap_or(false)
    });
    let fg = wins
        .iter()
        .find(|w| w.get("foreground").and_then(|v| v.as_bool()).unwrap_or(false))
        .cloned();
    json!({
        "count": count,
        "items": wins,
        "foreground": fg,
        "screen": {
            "w": unsafe { GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CXSCREEN) },
            "h": unsafe { GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CYSCREEN) },
        },
    })
}

fn find_window(title: &str) -> Option<(HWND, String)> {
    let needle = title.trim().to_lowercase();
    for w in collect_windows() {
        let t = w.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let p = w.get("process").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if needle.is_empty() {
            return None;
        }
        if t.to_lowercase().contains(&needle) || p.to_lowercase().contains(&needle) {
            let hwnd = w.get("hwnd").and_then(|v| v.as_i64()).unwrap_or(0) as isize as *mut core::ffi::c_void;
            return Some((HWND(hwnd), t));
        }
    }
    None
}


/// 把窗口抢到前台。Windows 有前台锁：不是用户正在交互的进程直接 SetForegroundWindow 会被拒。
/// 这里按成功率从高到低依次尝试，最后返回是否真的拿到前台（调用方必须校验，否则会把字打到别的窗口）。
fn force_foreground(hwnd: HWND) -> bool {
    unsafe {
        let _ = ShowWindow(hwnd, SW_RESTORE);
        let _ = AllowSetForegroundWindow(ASFW_ANY);
        // 伪造一次 ALT 按键：系统会把它当成用户输入，从而放开前台锁
        keybd_event(VK_MENU.0 as u8, 0, KEYBD_EVENT_FLAGS(0), 0);
        let mut pid = 0u32;
        let target_tid = GetWindowThreadProcessId(hwnd, Some(&mut pid));
        let current_tid = GetCurrentThreadId();
        let _ = AttachThreadInput(current_tid, target_tid, true);
        let _ = SetForegroundWindow(hwnd);
        let _ = AttachThreadInput(current_tid, target_tid, false);
        keybd_event(VK_MENU.0 as u8, 0, KEYEVENTF_KEYUP, 0);
        if GetForegroundWindow() == hwnd {
            return true;
        }
        // 兜底：SwitchToThisWindow（老 API，前台锁宽松）
        SwitchToThisWindow(hwnd, true);
        std::thread::sleep(std::time::Duration::from_millis(150));
        if GetForegroundWindow() == hwnd {
            return true;
        }
        // 再兜底：先置顶再取消置顶，很多情况下能把它带到 Z 序顶端并拿到焦点
        let _ = SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        let _ = SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW);
        let _ = BringWindowToTop(hwnd);
        let _ = SetForegroundWindow(hwnd);
        std::thread::sleep(std::time::Duration::from_millis(150));
        GetForegroundWindow() == hwnd
    }
}

fn foreground_title() -> String {
    let fg = unsafe { GetForegroundWindow() };
    if fg.0.is_null() {
        return String::new();
    }
    window_title(fg)
}

fn local_focus(title: &str) -> (bool, Value) {
    let Some((hwnd, found)) = find_window(title) else {
        return (
            false,
            json!({
                "error": format!("没找到标题含「{title}」的窗口"),
                "windows": collect_windows().into_iter().take(15).collect::<Vec<_>>(),
            }),
        );
    };
    let got = force_foreground(hwnd);
    std::thread::sleep(std::time::Duration::from_millis(200));
    let now = foreground_title();
    if !got {
        return (
            false,
            json!({
                "error": "没能把目标窗口切成前台（Windows 前台锁）",
                "window": found,
                "foreground_now": now,
                "hint": "可以让用户在目标窗口里点一下；或者改用 local_type 的 title 参数（会先聚焦再输入）",
            }),
        );
    }
    (true, json!({"focused": found, "foreground_now": now}))
}

// ---------------------------------------------------------------- 输入
fn send(inputs: &[INPUT]) -> u32 {
    if inputs.is_empty() {
        return 0;
    }
    unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) }
}

fn key_event(vk: VIRTUAL_KEY, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk,
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}

fn prepare_target(title: Option<&str>) -> Result<Option<String>, Value> {
    let Some(title) = title.map(str::trim).filter(|s| !s.is_empty()) else {
        return Ok(None);
    };
    let Some((hwnd, found)) = find_window(title) else {
        return Err(json!({
            "error": format!("没找到标题含「{title}」的窗口"),
            "windows": collect_windows().into_iter().take(12).collect::<Vec<_>>(),
        }));
    };
    if !force_foreground(hwnd) {
        return Err(json!({
            "error": "目标窗口没能切到前台，为避免把内容打到别的窗口，这次输入已取消",
            "window": found,
            "foreground_now": foreground_title(),
        }));
    }
    std::thread::sleep(std::time::Duration::from_millis(250));
    Ok(Some(foreground_title()))
}

fn local_type(text: &str, title: Option<&str>) -> (bool, Value) {
    if text.is_empty() {
        return (false, json!({"error": "text 不能为空"}));
    }
    let focused = match prepare_target(title) {
        Ok(v) => v,
        Err(err) => return (false, err),
    };
    let mut sent = 0usize;
    for ch in text.chars() {
        let mut buf = [0u16; 2];
        for unit in ch.encode_utf16(&mut buf) {
            let down = key_event(VIRTUAL_KEY(0), *unit, KEYEVENTF_UNICODE);
            let up = key_event(VIRTUAL_KEY(0), *unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP);
            let n = send(&[down, up]);
            if n == 0 {
                return (false, json!({"error": "SendInput 被拦截（目标窗口可能更高权限）", "sent": sent}));
            }
            sent += 1;
        }
    }
    (
        true,
        json!({
            "typed": text.chars().count(),
            "sent_units": sent,
            "target_focused": focused,
            "foreground_now": foreground_title(),
        }),
    )
}

fn parse_key(name: &str) -> Option<VIRTUAL_KEY> {
    let n = name.trim().to_lowercase();
    let vk = match n.as_str() {
        "ctrl" | "control" => 0x11,
        "alt" | "menu" => 0x12,
        "shift" => 0x10,
        "win" | "meta" | "cmd" => 0x5B,
        "enter" | "return" => 0x0D,
        "tab" => 0x09,
        "esc" | "escape" => 0x1B,
        "space" => 0x20,
        "backspace" | "bs" => 0x08,
        "delete" | "del" => 0x2E,
        "insert" => 0x2D,
        "home" => 0x24,
        "end" => 0x23,
        "pageup" => 0x21,
        "pagedown" => 0x22,
        "up" => 0x26,
        "down" => 0x28,
        "left" => 0x25,
        "right" => 0x27,
        "capslock" => 0x14,
        "prtsc" => 0x2C,
        s if s.len() == 1 => {
            let c = s.chars().next().unwrap();
            if c.is_ascii_alphabetic() {
                c.to_ascii_uppercase() as u32
            } else if c.is_ascii_digit() {
                c as u32
            } else {
                return None;
            }
        }
        s if s.starts_with('f') && s[1..].parse::<u32>().is_ok() => {
            0x70 + s[1..].parse::<u32>().unwrap() - 1
        }
        _ => return None,
    };
    Some(VIRTUAL_KEY(vk as u16))
}

fn local_keys(keys: &str, title: Option<&str>) -> (bool, Value) {
    let focused = match prepare_target(title) {
        Ok(v) => v,
        Err(err) => return (false, err),
    };
    let parts: Vec<&str> = keys.split('+').map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    if parts.is_empty() {
        return (false, json!({"error": "keys 不能为空，例：ctrl+s、enter、alt+tab"}));
    }
    let mut vks = Vec::new();
    for p in &parts {
        match parse_key(p) {
            Some(vk) => vks.push(vk),
            None => return (false, json!({"error": format!("不认识的键：{p}")})),
        }
    }
    // 依次按下，再逆序松开
    for vk in &vks {
        send(&[key_event(*vk, 0, KEYBD_EVENT_FLAGS(0))]);
        std::thread::sleep(std::time::Duration::from_millis(30));
    }
    for vk in vks.iter().rev() {
        send(&[key_event(*vk, 0, KEYEVENTF_KEYUP)]);
        std::thread::sleep(std::time::Duration::from_millis(30));
    }
    (
        true,
        json!({
            "sent": parts,
            "target_focused": focused,
            "foreground_now": foreground_title(),
        }),
    )
}

fn local_click(x: i32, y: i32, button: &str) -> (bool, Value) {
    let w = unsafe { GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CXSCREEN) };
    let h = unsafe { GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CYSCREEN) };
    if x < 0 || y < 0 || x >= w || y >= h {
        return (false, json!({"error": format!("坐标超出屏幕 {x},{y}（屏幕 {w}x{h}）")}));
    }
    let (down, up) = if button.eq_ignore_ascii_case("right") {
        (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    } else {
        (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    };
    let mx = (x as f64 / w as f64 * 65535.0) as i32;
    let my = (y as f64 / h as f64 * 65535.0) as i32;
    let mouse = |flags: MOUSE_EVENT_FLAGS| INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx: mx,
                dy: my,
                mouseData: 0,
                dwFlags: MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    send(&[mouse(MOUSE_EVENT_FLAGS(0))]);
    std::thread::sleep(std::time::Duration::from_millis(60));
    let n = send(&[mouse(down)]);
    std::thread::sleep(std::time::Duration::from_millis(40));
    send(&[mouse(up)]);
    if n == 0 {
        return (false, json!({"error": "SendInput 被拦截"}));
    }
    (
        true,
        json!({
            "clicked": {"x": x, "y": y, "button": button},
            "screen": {"w": w, "h": h},
        }),
    )
}

/// 关闭窗口：只对标题匹配的窗口发 WM_CLOSE（让应用自己走正常退出流程，不强杀进程）
fn close_window(title: &str) -> (bool, Value) {
    // 同一应用可能有多张窗口（Win11 记事本还有启动页），只关一张会留下"第二个记事本"
    let needle = title.trim().to_lowercase();
    let mut closed = Vec::new();
    for w in collect_windows() {
        let t = w.get("title").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
        let p = w.get("process").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
        if needle.is_empty() || !(t.contains(&needle) || p.contains(&needle)) {
            continue;
        }
        let raw = w.get("hwnd").and_then(|v| v.as_i64()).unwrap_or(0) as isize as *mut core::ffi::c_void;
        unsafe {
            let _ = PostMessageW(HWND(raw), WM_CLOSE, WPARAM(0), LPARAM(0));
        }
        closed.push(w.get("title").cloned().unwrap_or(json!("")));
    }
    if closed.is_empty() {
        return (false, json!({"error": format!("没找到标题含「{title}」的窗口")}));
    }
    // 关掉 ≠ 真的消失：Win11 记事本会弹"是否保存"，窗口还杵在那儿。
    // 这里必须回查一遍，否则模型会以为关成功了（它在真机上就被骗过一次）。
    let mut still_open = Vec::new();
    for _ in 0..6 {
        std::thread::sleep(std::time::Duration::from_millis(250));
        still_open = collect_windows()
            .into_iter()
            .filter(|w| {
                let tn = w.get("title").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                let pn = w.get("process").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                !needle.is_empty() && (tn.contains(&needle) || pn.contains(&needle))
            })
            .map(|w| w.get("title").cloned().unwrap_or(json!("")))
            .collect();
        if still_open.is_empty() {
            break;
        }
    }
    if !still_open.is_empty() {
        return (
            false,
            json!({
                "closed_requested": closed,
                "still_open": still_open,
                "error": "已发送关闭，但窗口没消失（多半弹了「是否保存」对话框）",
                "next": "用 local_keys 传 keys=alt+n、title=<窗口标题> 选「不保存」，或 local_exec taskkill 兜底；也可以 local_windows 看现在的窗口"
            }),
        );
    }
    (true, json!({"closed": closed}))
}

// ---------------------------------------------------------------- 截图
//
// 为什么要这一块：local_* 操作是"盲操作"——模型点完、打完字，没有任何办法确认
// 屏幕上真的发生了它以为的事。截图把这个反馈闭环补上。
//
// 三个硬约束：
//   * 工具返回体必须小（<1KB）：只回元数据，像素永远不进 tool_result，
//     否则它会顺着 tool.result 事件（payload 上限 8KB）和模型上下文一路放大；
//   * 像素要能到界面上：PNG 落到本机临时目录，由 main.rs 走 artifact 通道上传，
//     事件里只放 {id,url,bytes} 这种引用；
//   * 坐标要能换算：截图可能被缩小（4K 屏），所以回 scale + origin，
//     local_click 用的是屏幕绝对坐标，模型得知道怎么把截图里的点映射回去。

/// 默认最长边：1600 足够看清界面元素；再大 base64 上传量和 PNG 编码时间都会翻倍。
const DEFAULT_MAX_W: u64 = 1600;

fn epoch_millis() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}

fn primary_screen() -> (i32, i32, i32, i32) {
    unsafe {
        let w = GetSystemMetrics(SM_CXSCREEN);
        let h = GetSystemMetrics(SM_CYSCREEN);
        (0, 0, w, h)
    }
}

/// BitBlt 抓一块屏幕区域，返回 BGRA（每像素 4 字节，行自上而下）。
fn capture_rect(x: i32, y: i32, w: i32, h: i32) -> Result<Vec<u8>, String> {
    if w <= 0 || h <= 0 {
        return Err(format!("抓取区域为空（{w}x{h}）"));
    }
    // 防止有人传个巨大的窗口尺寸把内存吃光（4K 也不过 8M 像素）
    if (w as i64) * (h as i64) > 40_000_000 {
        return Err(format!("抓取区域过大（{w}x{h}）"));
    }
    unsafe {
        let screen = GetDC(HWND::default());
        if screen.is_invalid() {
            return Err("GetDC(NULL) 失败：拿不到屏幕句柄".into());
        }
        let mem = CreateCompatibleDC(screen);
        if mem.is_invalid() {
            let _ = ReleaseDC(HWND::default(), screen);
            return Err("CreateCompatibleDC 失败".into());
        }
        let bitmap = CreateCompatibleBitmap(screen, w, h);
        let out = if bitmap.is_invalid() {
            Err("CreateCompatibleBitmap 失败（显存/GDI 句柄不足）".into())
        } else {
            let old = SelectObject(mem, HGDIOBJ(bitmap.0));
            // CAPTUREBLT：把分层窗口（菜单阴影、输入法候选框）也一起抓进去
            let rop = ROP_CODE(SRCCOPY.0 | CAPTUREBLT.0);
            let blt = BitBlt(mem, 0, 0, w, h, screen, x, y, rop);
            let result = match blt {
                Err(err) => Err(format!("BitBlt 失败：{err}")),
                Ok(()) => read_pixels(mem, bitmap, w, h),
            };
            // 回收顺序不能错：先把位图从 DC 里摘出来，再删位图，否则位图泄漏
            let _ = SelectObject(mem, old);
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            result
        };
        let _ = DeleteDC(mem);
        let _ = ReleaseDC(HWND::default(), screen);
        out
    }
}

unsafe fn read_pixels(mem: HDC, bitmap: HBITMAP, w: i32, h: i32) -> Result<Vec<u8>, String> {
    let mut bmi = BITMAPINFO::default();
    bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
    bmi.bmiHeader.biWidth = w;
    bmi.bmiHeader.biHeight = -h; // 负高度 = 自上而下，省一次整幅翻转
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB.0;
    let mut buf = vec![0u8; w as usize * h as usize * 4];
    let lines = GetDIBits(
        mem,
        bitmap,
        0,
        h as u32,
        Some(buf.as_mut_ptr() as *mut core::ffi::c_void),
        &mut bmi,
        DIB_RGB_COLORS,
    );
    if lines != h {
        return Err(format!("GetDIBits 只取到 {lines}/{h} 行"));
    }
    Ok(buf)
}

/// GDI 给的是 BGRA，像素顺序反了；PNG 里存 RGB（不要 alpha：屏幕抓来的 A 通道是垃圾值，
/// 带 alpha 写出来在浏览器里会变成全透明）。
fn bgra_to_rgb(bgra: &[u8]) -> Vec<u8> {
    let mut rgb = vec![0u8; bgra.len() / 4 * 3];
    for (i, px) in bgra.chunks_exact(4).enumerate() {
        rgb[i * 3] = px[2];
        rgb[i * 3 + 1] = px[1];
        rgb[i * 3 + 2] = px[0];
    }
    rgb
}

/// 面积平均缩小（不是最近邻）：缩完的界面元素还能认出文字块，最近邻会碎成噪点。
fn downscale(rgb: &[u8], w: usize, h: usize, max_w: usize) -> (Vec<u8>, usize, usize) {
    if max_w == 0 || w <= max_w {
        return (rgb.to_vec(), w, h);
    }
    let nw = max_w;
    let nh = (((h as f64) * (nw as f64) / (w as f64)).round() as usize).max(1);
    let mut out = vec![0u8; nw * nh * 3];
    for y in 0..nh {
        let y0 = y * h / nh;
        let y1 = (((y + 1) * h) / nh).clamp(y0 + 1, h);
        for x in 0..nw {
            let x0 = x * w / nw;
            let x1 = (((x + 1) * w) / nw).clamp(x0 + 1, w);
            let mut acc = [0u32; 3];
            let mut n = 0u32;
            for sy in y0..y1 {
                let row = sy * w * 3;
                for sx in x0..x1 {
                    let i = row + sx * 3;
                    acc[0] += rgb[i] as u32;
                    acc[1] += rgb[i + 1] as u32;
                    acc[2] += rgb[i + 2] as u32;
                    n += 1;
                }
            }
            let o = (y * nw + x) * 3;
            out[o] = (acc[0] / n) as u8;
            out[o + 1] = (acc[1] / n) as u8;
            out[o + 2] = (acc[2] / n) as u8;
        }
    }
    (out, nw, nh)
}

fn encode_png(
    rgb: &[u8],
    w: usize,
    h: usize,
    compression: png::Compression,
) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(&mut out, w as u32, h as u32);
        enc.set_color(png::ColorType::Rgb);
        enc.set_depth(png::BitDepth::Eight);
        enc.set_compression(compression);
        let mut writer = enc.write_header().map_err(|e| format!("png header: {e}"))?;
        writer
            .write_image_data(rgb)
            .map_err(|e| format!("png data: {e}"))?;
    }
    Ok(out)
}

/// 抓一块屏幕并转成 RGB（截图主流程和自检基准都用它，保证测的就是真实路径）。
fn grab_rgb(x: i32, y: i32, w: i32, h: i32) -> Result<(Vec<u8>, usize, usize), String> {
    let bgra = capture_rect(x, y, w, h)?;
    Ok((bgra_to_rgb(&bgra), w as usize, h as usize))
}

// PrintWindow 不在 windows 0.58 的绑定里，直接声明（user32 已随 windows 链接）。
#[link(name = "user32")]
extern "system" {
    fn PrintWindow(hwnd: *mut core::ffi::c_void, hdc: *mut core::ffi::c_void, flags: u32) -> i32;
}

/// 按窗口句柄抓整窗（含标题栏），被别的窗口挡住也能抓到内容。
///
/// 为什么不能只按屏幕矩形截：BitBlt 屏幕 DC 抓的是**当前合成画面**，目标窗口被遮挡时
/// 截出来的是遮挡者，而"这个窗口现在什么状态"恰恰是被挡住时最想知道的事。
/// 为什么不用 BitBlt(窗口 DC)：DWM 下窗口 DC 拿到的是空白表面（实测整幅发白），
/// 正确的是 PrintWindow + PW_RENDERFULLCONTENT(2)，让 DWM 把完整内容画进内存 DC。
/// windows 0.58 没绑定 PrintWindow，这里直接声明 extern（user32 本来就链接了）。
unsafe fn capture_window(hwnd: HWND, w: i32, h: i32) -> Result<Vec<u8>, String> {
    if w <= 0 || h <= 0 {
        return Err(format!("窗口尺寸无效（{w}x{h}）"));
    }
    let screen = GetDC(HWND::default());
    if screen.is_invalid() {
        return Err("GetDC(NULL) 失败".into());
    }
    let mem = CreateCompatibleDC(screen);
    if mem.is_invalid() {
        let _ = ReleaseDC(HWND::default(), screen);
        return Err("CreateCompatibleDC 失败".into());
    }
    let bitmap = CreateCompatibleBitmap(screen, w, h);
    let out = if bitmap.is_invalid() {
        Err("CreateCompatibleBitmap 失败".into())
    } else {
        let old = SelectObject(mem, HGDIOBJ(bitmap.0));
        let ok = PrintWindow(hwnd.0, mem.0, 2 /* PW_RENDERFULLCONTENT */);
        let result = if ok == 0 {
            Err("PrintWindow 返回失败".into())
        } else {
            read_pixels(mem, bitmap, w, h)
        };
        let _ = SelectObject(mem, old);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        result
    };
    let _ = DeleteDC(mem);
    let _ = ReleaseDC(HWND::default(), screen);
    out
}

/// 整幅只有一种颜色 = 没拿到内容（PrintWindow 对个别硬件加速窗口会给出全白/全黑），
/// 这时要退回屏幕矩形截取，否则会把一张空白图当成功结果交出去。
fn is_uniform(bgra: &[u8]) -> bool {
    let mut first: Option<[u8; 3]> = None;
    let mut total = 0usize;
    for px in bgra.chunks_exact(4).step_by(32) {
        total += 1;
        match first {
            None => first = Some([px[0], px[1], px[2]]),
            Some(c) => {
                if (px[0] as i32 - c[0] as i32).abs() > 3
                    || (px[1] as i32 - c[1] as i32).abs() > 3
                    || (px[2] as i32 - c[2] as i32).abs() > 3
                {
                    return false;
                }
            }
        }
    }
    total > 0
}

/// 截图落盘目录候选：TEMP 优先（本来就是临时文件），被策略挡住时退到 AppData，
/// 最后退到当前目录。企业管控的机器上 TEMP 不可写不是罕见情况，不能因此让截图整个挂掉。
fn shot_dir_candidates() -> Vec<PathBuf> {
    let mut dirs = vec![std::env::temp_dir().join("agentos").join("shots")];
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        dirs.push(Path::new(&local).join("AgentOS").join("shots"));
    }
    if let Ok(cwd) = std::env::current_dir() {
        dirs.push(cwd.join(".agentos-shots"));
    }
    dirs
}

/// 写到第一个能写的位置，返回真实路径。
pub(crate) fn write_anywhere(name: &str, data: &[u8]) -> Result<PathBuf, String> {
    let mut last = String::from("没有可用的截图目录");
    for dir in shot_dir_candidates() {
        if let Err(err) = std::fs::create_dir_all(&dir) {
            last = format!("{}: {err}", dir.display());
            continue;
        }
        let path = dir.join(name);
        match std::fs::write(&path, data) {
            Ok(()) => return Ok(path),
            Err(err) => last = format!("{}: {err}", path.display()),
        }
    }
    Err(last)
}

fn local_screenshot(args: &Value) -> (bool, Value) {
    let started = std::time::Instant::now();
    let max_w = args
        .get("max_w")
        .and_then(|v| v.as_u64())
        .unwrap_or(DEFAULT_MAX_W) as usize;
    let want_window = args
        .get("window_title")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty());

    // 1) 决定抓哪一块。窗口截图保留两份矩形：
    //    * full_w/full_h + rect 左上角：窗口自己的表面（被遮挡也能抓）
    //    * clip 后的 (x,y,w,h)：退回屏幕截取时用（裁到屏幕内）
    let mut window_hwnd: Option<HWND> = None;
    let mut full: (i32, i32, i32, i32) = (0, 0, 0, 0);
    let (x, y, w, h, source, window) = match want_window {
        Some(title) => {
            let Some((hwnd, found)) = find_window(title) else {
                return (
                    false,
                    json!({
                        "error": format!("没找到标题含「{title}」的窗口"),
                        "windows": collect_windows().into_iter().take(12).collect::<Vec<_>>(),
                    }),
                );
            };
            let mut rect = RECT::default();
            if let Err(err) = unsafe { GetWindowRect(hwnd, &mut rect) } {
                return (false, json!({"error": format!("GetWindowRect: {err}"), "window": found}));
            }
            let (scr_w, scr_h) =
                unsafe { (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN)) };
            let rect_w = rect.right - rect.left;
            let rect_h = rect.bottom - rect.top;
            let x0 = rect.left.max(0);
            let y0 = rect.top.max(0);
            let x1 = rect.right.min(scr_w);
            let y1 = rect.bottom.min(scr_h);
            if rect_w < 8 || rect_h < 8 || x1 - x0 < 8 || y1 - y0 < 8 {
                return (
                    false,
                    json!({
                        "error": "目标窗口已最小化或完全在屏幕外，截不到内容",
                        "window": found,
                        "rect": [rect.left, rect.top, rect_w, rect_h],
                        "screen": {"w": scr_w, "h": scr_h},
                    }),
                );
            }
            window_hwnd = Some(hwnd);
            full = (rect.left, rect.top, rect_w, rect_h);
            (
                x0,
                y0,
                (x1 - x0).max(1),
                (y1 - y0).max(1),
                "window",
                Some(json!({"title": found, "rect": [rect.left, rect.top, rect_w, rect_h]})),
            )
        }
        None => {
            let (x, y, w, h) = primary_screen();
            (x, y, w, h, "screen", None)
        }
    };

    // 2) 抓像素：窗口优先走 PrintWindow（被遮挡也对），拿不到内容再退回屏幕矩形
    let (bgra, cap_w, cap_h, cap_x, cap_y, via) = match window_hwnd {
        Some(hwnd) => match unsafe { capture_window(hwnd, full.2, full.3) } {
            Ok(buf) if !is_uniform(&buf) => (buf, full.2, full.3, full.0, full.1, "print_window"),
            _ => match capture_rect(x, y, w, h) {
                Ok(buf) => (buf, w, h, x, y, "screen_rect"),
                Err(err) => return (false, json!({"error": err})),
            },
        },
        None => match capture_rect(x, y, w, h) {
            Ok(buf) => (buf, w, h, x, y, "screen"),
            Err(err) => return (false, json!({"error": err})),
        },
    };

    // 3) 转 RGB → 缩放 → 编码 PNG
    let rgb = bgra_to_rgb(&bgra);
    let (rgb, out_w, out_h) = downscale(&rgb, cap_w as usize, cap_h as usize, max_w);
    let png = match encode_png(&rgb, out_w, out_h, png::Compression::Default) {
        Ok(v) => v,
        Err(err) => return (false, json!({"error": err})),
    };

    // 4) 落盘（临时目录，由一个独立清理任务/用户手动清理；不写用户文档目录）
    let name = format!("shot_{}.png", epoch_millis());
    let path = match write_anywhere(&name, &png) {
        Ok(p) => p,
        Err(err) => return (false, json!({"error": format!("写 PNG 失败：{err}")})),
    };

    let ms = started.elapsed().as_millis() as u64;
    (
        true,
        json!({
            "ok": true,
            "path": path.display().to_string(),
            "name": name,
            "kind": "image/png",
            "width": out_w,
            "height": out_h,
            "source_width": cap_w,
            "source_height": cap_h,
            // 截图坐标 × 1/scale + origin = 屏幕绝对坐标（local_click / local_type 用后者）
            "scale": if out_w == 0 { 1.0 } else { out_w as f64 / cap_w as f64 },
            "origin": {"x": cap_x, "y": cap_y},
            "bytes": png.len(),
            "ms": ms,
            "source": source,
            // window_dc = 直接抓窗口表面（被遮挡也准）；screen_rect = 只抓到屏幕上该区域（可能被挡）
            "captured_via": via,
            "window": window,
            "active_window": foreground_title(),
            "next": "像素已另存为图片并挂到会话里（事件只带引用）；要点击某个位置，屏幕坐标 = origin + 截图坐标 / scale",
        }),
    )
}

/// 无窗口自检：`agentos-desktop.exe --selftest-screenshot`
/// 抓一次全屏（走和生产完全一样的路径）+ 跑一组体积/耗时基准，结果写成 JSON。
/// 打包后是 windows 子系统（没有控制台），所以结果落文件而不是 stdout。
pub fn selftest() {
    let started = std::time::Instant::now();
    let (ok, value) = local_screenshot(&json!({}));

    // 同一帧像素，不同 max_w / 压缩级别的代价对比——默认值就是照这张表定的
    let mut bench: Vec<Value> = Vec::new();
    if let Ok((rgb, w, h)) = grab_rgb(0, 0, unsafe { GetSystemMetrics(SM_CXSCREEN) }, unsafe {
        GetSystemMetrics(SM_CYSCREEN)
    }) {
        for (max_w, level, comp) in [
            (1600usize, "fast", png::Compression::Fast),
            (1600, "default", png::Compression::Default),
            (1280, "default", png::Compression::Default),
            (1280, "best", png::Compression::Best),
            (1024, "default", png::Compression::Default),
        ] {
            let (small, sw, sh) = downscale(&rgb, w, h, max_w);
            let t = std::time::Instant::now();
            let bytes = encode_png(&small, sw, sh, comp).map(|v| v.len()).unwrap_or(0);
            bench.push(json!({
                "max_w": max_w, "compression": level,
                "width": sw, "height": sh,
                "png_bytes": bytes,
                "base64_bytes": (bytes + 2) / 3 * 4,
                "encode_ms": t.elapsed().as_millis() as u64,
            }));
        }
    }

    let payload = json!({
        "ok": ok,
        "elapsed_ms": started.elapsed().as_millis() as u64,
        "screen": {"w": unsafe { GetSystemMetrics(SM_CXSCREEN) },
                   "h": unsafe { GetSystemMetrics(SM_CYSCREEN) }},
        "shot": value,
        "bench": bench,
    });
    let text = serde_json::to_string_pretty(&payload).unwrap_or_else(|_| "{}".into());
    match write_anywhere("selftest.json", text.as_bytes()) {
        Ok(path) => println!("selftest written to {}", path.display()),
        Err(err) => println!("selftest 写入失败：{err}\n{text}"),
    }
}
