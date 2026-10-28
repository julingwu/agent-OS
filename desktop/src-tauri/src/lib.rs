#[cfg(windows)]
use std::path::Path;
#[cfg(windows)]
use std::process::{Command, Stdio};
#[cfg(windows)]
use std::time::Duration;

#[cfg(windows)]
mod gui;

#[cfg(not(windows))]
mod gui {
    use serde_json::Value;
    pub fn handle(_tool: &str, _args: &Value) -> Option<(bool, Value)> {
        None
    }
    pub fn selftest() {}
    pub fn write_anywhere(_name: &str, _bytes: &[u8]) -> std::io::Result<std::path::PathBuf> {
        Err(std::io::Error::new(std::io::ErrorKind::Unsupported, "not supported"))
    }
}

use serde_json::{json, Value};
use tauri::Manager;
#[cfg(desktop)]
use tauri::WebviewWindowBuilder;
#[cfg(windows)]
use tungstenite::client::IntoClientRequest;
#[cfg(windows)]
use tungstenite::Message;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let log_path = std::env::temp_dir().join("agentos_boot.log");
    let append_log = |msg: &str| {
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
            let _ = writeln!(f, "{}", msg);
        }
    };

    append_log("--- AgentOS booting ---");

    std::panic::set_hook(Box::new(|info| {
        let path = std::env::temp_dir().join("agentos_boot.log");
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            let _ = writeln!(f, "[PANIC] {info}");
        }
    }));

    let cli: Vec<String> = std::env::args().collect();
    append_log(&format!("Args: {:?}", cli));
    // 无窗口自检（不启动 Tauri）：`agentos-desktop.exe --selftest-screenshot`
    // 用来在真机上验证 GDI 抓屏 + PNG 编码是否可用，结果写 %TEMP%\agentos\shots\selftest.json
    #[cfg(windows)]
    if cli.iter().any(|a| a == "--selftest-screenshot") {
        gui::selftest();
        return;
    }
    // 端到端自检：`--selftest-bridge <base_url> <token> [max_calls]`
    // 不启动界面直接连本地桥，由测试端下发 tool_call，验证 artifact 上传链路。
    #[cfg(windows)]
    if let Some(pos) = cli.iter().position(|a| a == "--selftest-bridge") {
        let base = cli.get(pos + 1).cloned().unwrap_or_default();
        let token = cli.get(pos + 2).cloned().unwrap_or_default();
        let max_calls = cli
            .get(pos + 3)
            .and_then(|v| v.parse::<usize>().ok())
            .unwrap_or(1);
        let report = selftest_bridge(&base, &token, max_calls);
        let text = serde_json::to_string_pretty(&report).unwrap_or_else(|_| "{}".into());
        // 走和截图同一套可写目录兜底（TEMP → AppData → 当前目录）
        let _ = gui::write_anywhere("selftest_bridge.json", text.as_bytes());
        return;
    }
    let result = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![local_bridge_start])
        .setup(|app| {
            #[cfg(desktop)]
            {
                let data_dir = app.path().app_local_data_dir().ok();
                let log_path = std::env::temp_dir().join("agentos_boot.log");
                let append_setup = |msg: &str| {
                    use std::io::Write;
                    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
                        let _ = writeln!(f, "[setup] {}", msg);
                    }
                };
                append_setup(&format!("data_dir: {:?}", data_dir));

                let mut builder = WebviewWindowBuilder::new(
                    app,
                    "main",
                    tauri::WebviewUrl::App("index.html".into()),
                )
                .title("AgentOS")
                .inner_size(1320.0, 860.0);

                // 证书与调试参数
                #[cfg(target_os = "windows")]
                {
                    let mut browser_args: Vec<String> = Vec::new();
                    if std::env::var("AGENTOS_IGNORE_CERT").is_ok() {
                        browser_args.push("--ignore-certificate-errors".to_string());
                    }
                    if std::env::var("AGENTOS_DEBUG_PORT").is_ok() {
                        browser_args.push("--remote-debugging-port=9333".to_string());
                    }
                    if !browser_args.is_empty() {
                        let args = browser_args.join(" ");
                        builder = builder.additional_browser_args(&args);
                    }
                }

                match builder.build() {
                    Ok(_) => append_setup("window created successfully"),
                    Err(err) => {
                        append_setup(&format!("WINDOW ERROR: {err}, attempting fallback window..."));
                        let fallback_dir = std::env::temp_dir().join("agentos_wv2_fresh");
                        let _ = std::fs::create_dir_all(&fallback_dir);
                        let fallback_builder = WebviewWindowBuilder::new(
                            app,
                            "main",
                            tauri::WebviewUrl::App("index.html".into()),
                        )
                        .title("AgentOS")
                        .inner_size(1320.0, 860.0)
                        .data_directory(fallback_dir);

                        match fallback_builder.build() {
                            Ok(_) => append_setup("fallback window created successfully"),
                            Err(e2) => append_setup(&format!("FALLBACK WINDOW ERROR: {e2}")),
                        }
                    }
                }
            }
            Ok(())
        })
        .run(tauri::generate_context!());
    if let Err(err) = result {
        let log_path = std::env::temp_dir().join("agentos_boot.log");
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
            let _ = writeln!(f, "[tauri error] {err}");
        }
        eprintln!("[boot] tauri run error: {err}");
    }
}

/// 前端登录后调用：把服务器地址 + 设备令牌交给本地桥，建立出站连接。
/// 断线自动重连（5 秒间隔），保证网络抖动后本地能力自己恢复。
#[cfg(windows)]
#[tauri::command]
fn local_bridge_start(base_url: String, token: String) {
    std::thread::spawn(move || loop {
        match run_bridge(&base_url, &token) {
            Ok(()) => eprintln!("[local-bridge] closed by server"),
            Err(err) => eprintln!("[local-bridge] {err}"),
        }
        std::thread::sleep(Duration::from_secs(5));
    });
}

#[cfg(not(windows))]
#[tauri::command]
fn local_bridge_start(_base_url: String, _token: String) {
    // 移动端/非Windows端仅作为UI控制终端，不作为桌面本地桥执行节点
}

#[cfg(windows)]
fn ws_url(base_url: &str, token: &str) -> String {
    let base = base_url.trim_end_matches('/');
    let scheme_swapped = if let Some(rest) = base.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = base.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        base.to_string()
    };
    format!("{scheme_swapped}/v1/device?token={token}")
}

#[cfg(windows)]
type BridgeSocket = tungstenite::WebSocket<native_tls::TlsStream<std::net::TcpStream>>;

#[cfg(windows)]
fn connect_bridge(base_url: &str, token: &str) -> Result<BridgeSocket, String> {
    let url = ws_url(base_url, token);
    let parsed = url::Url::parse(&url).map_err(|e| format!("url: {e}"))?;
    let host = parsed.host_str().ok_or("url has no host")?.to_string();
    let port = parsed.port_or_known_default().ok_or("url has no port")?;
    let tcp = std::net::TcpStream::connect((host.as_str(), port))
        .map_err(|e| format!("tcp: {e}"))?;
    let ignore_cert = std::env::var("AGENTOS_IGNORE_CERT").is_ok();
    let tls = native_tls::TlsConnector::builder()
        .danger_accept_invalid_certs(ignore_cert)
        .build()
        .map_err(|e| format!("tls: {e}"))?;
    let tls_stream = tls.connect(&host, tcp).map_err(|e| format!("tls connect: {e}"))?;
    let request = url
        .into_client_request()
        .map_err(|e| format!("request: {e}"))?;
    let (socket, _resp) = tungstenite::client::client(request, tls_stream)
        .map_err(|e| format!("handshake: {e}"))?;
    Ok(socket)
}

#[cfg(windows)]
fn send_hello(socket: &mut BridgeSocket) -> Result<(), String> {
    let hello = json!({"t": "hello", "caps": ["exec", "fs", "info", "gui", "screenshot"], "platform": std::env::consts::OS});
    socket
        .send(Message::Text(hello.to_string()))
        .map_err(|e| format!("hello: {e}"))
}

#[cfg(windows)]
fn run_bridge(base_url: &str, token: &str) -> Result<(), String> {
    let mut socket = connect_bridge(base_url, token)?;
    send_hello(&mut socket)?;

    loop {
        let message = match socket.read() {
            Ok(m) => m,
            Err(err) => return Err(format!("read: {err}")),
        };
        match message {
            Message::Text(text) => {
                let value: Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                if value.get("t").and_then(|v| v.as_str()) == Some("tool_call") {
                    serve_tool_call(&mut socket, &value)?;
                }
            }
            Message::Ping(payload) => {
                let _ = socket.send(Message::Pong(payload));
            }
            Message::Close(_) => return Ok(()),
            _ => {}
        }
    }
}

/// 处理一条 tool_call：**先**推 artifact（截图字节），**再**回只带元数据的 tool_result。
///
/// 顺序不能反：服务端要靠 call_id 找到这次调用挂靠的会话，调用一结束上下文就被回收。
/// 返回值是给自检用的调用摘要。
#[cfg(windows)]
fn serve_tool_call(socket: &mut BridgeSocket, value: &Value) -> Result<Value, String> {
    let call_id = value.get("call_id").and_then(|v| v.as_str()).unwrap_or("");
    let tool = value.get("tool").and_then(|v| v.as_str()).unwrap_or("");
    let args = value.get("args").cloned().unwrap_or(json!({}));
    let (ok, payload) = handle_call(tool, &args);

    let mut artifact = Value::Null;
    if ok {
        if let Some(msg) = artifact_message(call_id, tool, &payload) {
            let b64_chars = msg
                .get("data_b64")
                .and_then(|v| v.as_str())
                .map(|s| s.len())
                .unwrap_or(0);
            socket
                .send(Message::Text(msg.to_string()))
                .map_err(|e| format!("send artifact: {e}"))?;
            artifact = json!({
                "name": msg.get("name").cloned().unwrap_or(Value::Null),
                "kind": msg.get("kind").cloned().unwrap_or(Value::Null),
                "bytes": msg.get("bytes").cloned().unwrap_or(Value::Null),
                "data_b64_chars": b64_chars,
            });
        }
    }

    let reply = if ok {
        json!({"t": "tool_result", "call_id": call_id, "ok": true, "result": payload})
    } else {
        // 失败也要把完整结果带回去（exit_code/stderr/windows…），
        // 只回一个 "failed" 会让模型瞎猜原因，甚至编造结论
        let err = payload
            .get("error")
            .cloned()
            .unwrap_or_else(|| payload.clone());
        json!({"t": "tool_result", "call_id": call_id, "ok": false,
               "result": payload, "error": err})
    };
    socket
        .send(Message::Text(reply.to_string()))
        .map_err(|e| format!("send result: {e}"))?;

    Ok(json!({
        "call_id": call_id,
        "tool": tool,
        "ok": ok,
        "artifact": artifact,
        "result": if ok { payload } else { Value::Null },
    }))
}

/// 端到端自检：`--selftest-bridge <base_url> <token>`
/// 不启动界面，直接连服务端 `/v1/device`，由测试端下发 tool_call，
/// 把每条调用（含 artifact 的字节数 / b64 长度）写成 JSON，验证 exe 侧链路是否通。
#[cfg(windows)]
fn selftest_bridge(base_url: &str, token: &str, max_calls: usize) -> Value {
    let mut out = json!({"ok": false, "base_url": base_url, "calls": [], "errors": []});
    let mut socket = match connect_bridge(base_url, token) {
        Ok(s) => s,
        Err(err) => {
            out["errors"] = json!([err]);
            return out;
        }
    };
    if let Err(err) = send_hello(&mut socket) {
        out["errors"] = json!([err]);
        return out;
    }
    out["connected"] = json!(true);

    let mut calls: Vec<Value> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    while calls.len() < max_calls.max(1) {
        match socket.read() {
            Ok(Message::Text(text)) => {
                let value: Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                if value.get("t").and_then(|v| v.as_str()) != Some("tool_call") {
                    continue;
                }
                match serve_tool_call(&mut socket, &value) {
                    Ok(summary) => calls.push(summary),
                    Err(err) => {
                        errors.push(err);
                        break;
                    }
                }
            }
            Ok(Message::Ping(payload)) => {
                let _ = socket.send(Message::Pong(payload));
            }
            Ok(Message::Close(_)) => break,
            Ok(_) => {}
            Err(err) => {
                errors.push(format!("read: {err}"));
                break;
            }
        }
    }
    out["ok"] = json!(calls.iter().all(|c| c["ok"].as_bool().unwrap_or(false))
        && errors.is_empty()
        && !calls.is_empty());
    out["calls"] = json!(calls);
    out["errors"] = json!(errors);
    out
}

#[cfg(windows)]
fn handle_call(tool: &str, args: &Value) -> (bool, Value) {
    if let Some(result) = gui::handle(tool, args) {
        return result;
    }
    match tool {
        "local_info" => (true, local_info()),
        "local_list" => match args.get("path").and_then(|v| v.as_str()) {
            Some(path) => local_list(path),
            None => (false, json!({"error": "path required"})),
        },
        "local_read" => {
            let path = args.get("path").and_then(|v| v.as_str()).unwrap_or("");
            let max = args.get("max_bytes").and_then(|v| v.as_u64()).unwrap_or(200_000);
            local_read(path, max)
        }
        "local_write" => {
            let path = args.get("path").and_then(|v| v.as_str()).unwrap_or("");
            let content = args.get("content").and_then(|v| v.as_str()).unwrap_or("");
            local_write(path, content)
        }
        "local_exec" => {
            let cmd = args.get("cmd").and_then(|v| v.as_str()).unwrap_or("");
            let cwd = args.get("cwd").and_then(|v| v.as_str());
            let timeout = args.get("timeout_s").and_then(|v| v.as_u64()).unwrap_or(60);
            local_exec(cmd, cwd, timeout)
        }
        _ => (false, json!({"error": format!("unknown local tool: {tool}")})),
    }
}

/// 单条 artifact 的原始字节上限：uvicorn 的 ws 帧默认 16MB，base64 会放大 4/3，
/// 再加上 JSON 头 ~300B。超过就不推了（宁可让用户看到元数据，也不要把桥撑断）。
#[cfg(windows)]
const MAX_ARTIFACT_BYTES: usize = 8 * 1024 * 1024;

/// 工具产生本地文件时，把字节单独推一条 {"t":"artifact",...}。
/// 事件 payload 上限 8KB（app/events.py MAX_PAYLOAD_BYTES），图片绝不能内联进 tool_result。
#[cfg(windows)]
fn artifact_message(call_id: &str, tool: &str, payload: &Value) -> Option<Value> {
    if tool != "local_screenshot" {
        return None;
    }
    let path = payload.get("path").and_then(|v| v.as_str())?;
    let data = std::fs::read(path).ok()?;
    if data.is_empty() || data.len() > MAX_ARTIFACT_BYTES {
        return None;
    }
    let name = payload
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("screenshot.png");
    let kind = payload
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("image/png");
    Some(json!({
        "t": "artifact",
        "call_id": call_id,
        "name": name,
        "kind": kind,
        "bytes": data.len(),
        "data_b64": base64_encode(&data),
    }))
}

/// 手写 base64：只为这一处需求引一个 crate 不值得（依赖越少，打包越稳）。
#[cfg(windows)]
fn base64_encode(data: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        out.push(if chunk.len() > 1 {
            TABLE[((n >> 6) & 63) as usize] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            TABLE[(n & 63) as usize] as char
        } else {
            '='
        });
    }
    out
}

#[cfg(windows)]
fn local_info() -> Value {
    let env = |k: &str| std::env::var(k).unwrap_or_default();
    json!({
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "hostname": env("COMPUTERNAME"),
        "username": env("USERNAME"),
        "home": env("USERPROFILE"),
        "cwd": std::env::current_dir().map(|p| p.display().to_string()).unwrap_or_default(),
    })
}

#[cfg(windows)]
fn local_list(path: &str) -> (bool, Value) {
    let target = Path::new(path);
    let entries = match std::fs::read_dir(target) {
        Ok(iter) => iter,
        Err(err) => return (false, json!({"error": format!("read_dir: {err}")})),
    };
    let mut items = Vec::new();
    for entry in entries.flatten().take(300) {
        let meta = entry.metadata().ok();
        items.push(json!({
            "name": entry.file_name().to_string_lossy(),
            "type": if meta.as_ref().map(|m| m.is_dir()).unwrap_or(false) { "dir" } else { "file" },
            "bytes": meta.as_ref().map(|m| m.len()).unwrap_or(0),
        }));
    }
    (true, json!({"path": path, "items": items}))
}

#[cfg(windows)]
fn local_read(path: &str, max_bytes: u64) -> (bool, Value) {
    let data = match std::fs::read(path) {
        Ok(d) => d,
        Err(err) => return (false, json!({"error": format!("read: {err}")})),
    };
    let slice = &data[..data.len().min(max_bytes as usize)];
    if slice.contains(&0u8) {
        return (false, json!({"error": "binary file"}));
    }
    let text = String::from_utf8_lossy(slice).to_string();
    (true, json!({"path": path, "content": text, "bytes": data.len()}))
}

#[cfg(windows)]
fn local_write(path: &str, content: &str) -> (bool, Value) {
    let target = Path::new(path);
    if let Some(parent) = target.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match std::fs::write(target, content.as_bytes()) {
        Ok(()) => (true, json!({"path": path, "bytes": content.len()})),
        Err(err) => (false, json!({"error": format!("write: {err}")})),
    }
}

#[cfg(windows)]
fn local_exec(cmd: &str, cwd: Option<&str>, timeout_s: u64) -> (bool, Value) {
    if cmd.trim().is_empty() {
        return (false, json!({"error": "empty command"}));
    }
    let mut command = if cfg!(windows) {
        let mut c = Command::new("cmd");
        c.arg("/C").arg(cmd);
        c
    } else {
        let mut c = Command::new("sh");
        c.arg("-c").arg(cmd);
        c
    };
    if let Some(dir) = cwd {
        command.current_dir(dir);
    }
    command.stdout(Stdio::piped()).stderr(Stdio::piped());

    let child = match command.spawn() {
        Ok(c) => c,
        Err(err) => return (false, json!({"error": format!("spawn: {err}")})),
    };
    // 简单超时：轮询等待；超时杀掉进程
    let start = std::time::Instant::now();
    let mut child = child;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let output = child.wait_with_output();
                let (stdout, stderr) = match output {
                    Ok(out) => (
                        String::from_utf8_lossy(&out.stdout).to_string(),
                        String::from_utf8_lossy(&out.stderr).to_string(),
                    ),
                    Err(_) => (String::new(), String::new()),
                };
                return (
                    status.success(),
                    json!({
                        "ok": status.success(),
                        "exit_code": status.code(),
                        "stdout": stdout.chars().take(8000).collect::<String>(),
                        "stderr": stderr.chars().take(8000).collect::<String>(),
                    }),
                );
            }
            Ok(None) => {
                if start.elapsed() > Duration::from_secs(timeout_s.max(1)) {
                    let _ = child.kill();
                    return (false, json!({"error": format!("timeout after {timeout_s}s")}));
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            Err(err) => return (false, json!({"error": format!("wait: {err}")})),
        }
    }
}
