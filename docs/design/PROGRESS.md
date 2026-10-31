# 施工进度（PROGRESS）

> 唯一范围基线：`feature-parity.md`｜契约：`contracts/01-events.md`、`02-processes.md`、`03-projection-api.md`
> 施工顺序见 `build-spec.md` 第 3 节。**不分版本期，按依赖连续推进。**

| 工作包 | 状态 | 交付物 | 验证 |
|---|---|---|---|
| W0 底座（Linux 环境/证书/防火墙/磁盘） | 🟡 服务器已重装并可用（桌面已就绪，防火墙/证书待收尾） | `scripts/provision_debian_desktop.sh`、`scripts/provision_debian_desktop_fix.sh` | XFCE `:0` 1600×900 截图、lightdm/xrdp/x11vnc active |
| W1 数据层与事件原语 | ✅ 完成 | `app/schema_v1.sql`、`app/events.py` | `tests/test_events_v1.py` 14 通过 |
| W2 事件流服务（REST + WSS 断线续传） | ✅ 完成 | `app/devices.py`、`app/api_v1/stream.py`，挂在 `app.main` | `tests/test_stream_v1.py` 8 通过 |
| W3 容器电脑（镜像/生命周期/截图/接管） | 🟡 **改为宿主机桌面**：截图/状态/推流已完成；接管(VNC)待接 | `app/computer/screen.py`、`app/api_v1/computer.py` | `tests/test_screen_v1.py` 8 通过 + 服务器实拍 |
| W4 进程 supervisor | ⬜ 未开始（跨平台抽象） | — | — |
| W5 Agent 内核与工具 | ✅ 完成（6 工具：exec/fs_*/computer_info/ask_human） | `app/agent/loop.py`、`tools.py` | `tests/test_agent_v1.py` 10 通过 + 服务器真实对话 |
| W6 Bot/会话/群/交接 | 🟡 Bot/会话/消息 API 完成；群路由/交接待做 | `app/api_v1/bots.py`、`seed.py`、`workspace.py` | `tests/test_bots_v1.py` 9 通过 + 服务器实测 |
| W7 例行任务/技能/资料库 | 🟡 资料库（工作区浏览）完成；routines/skills 待做 | `app/api_v1/workspace.py` | 服务器实测 |
| W8 投影 API + exe | ✅ **exe 跑通**（Tauri 2，6.1MB）：投影 + 本地桥（exec/fs/桌面操控）+ 逐次审批；证书固定/自动更新待做 | `desktop/`、`desktop/src-tauri/`、`/ui/` | `build/shots/local_notepad3.png`（本机记事本被 AI 输入成功） |
| W9 手机/推送/收尾 | ⬜ 未开始 | — | — |
| W7 例行任务/技能/资料库 | ⬜ 未开始 | — | — |
| W8 投影 API + exe | ⬜ 未开始 | — | — |
| W9 手机/推送/收尾 | ⬜ 未开始 | — | — |

## 已完成的关键约定（实现层）

### 服务器现状（2026-10-26 23:45）
- 腾讯云 Lighthouse `lhins-f4zsj3q9`，Debian 13.2 (trixie)，内核 6.12.86，4C/8G/59G（已用 8%）
- 公网 `203.0.113.10`；SSH 密钥：`~/.ssh/oa_agent_ed25519.pem`（KeyId `example-key-id`）
- 桌面：XFCE on `:0` @1600×900，lightdm autologin `oaagent`，xrdp 3389，x11vnc 5900（仅 localhost）
- **服务端已上线**：`https://203.0.113.10/` → nginx(443, 自签) → uvicorn(127.0.0.1:8077)
  - TLS 指纹 SHA256：`C7:0D:05:48:13:7A:26:A8:E5:33:86:EB:43:D1:98:6C:E4:B5:2C:67:78:23:40:C4:8A:B6:A0:C6:D0:68:96:53`
  - systemd：`agent-os.service`；代码 `/opt/agent-os/app-src`；数据 `/opt/agent-os/data/oa.db`；工作区 `/opt/agent-os/workspace`
  - Python 3.13.5 + fastapi 0.162.0；`/v1/health` 200；`/v1/events` 需 Bearer 令牌（未带 401）
  - 设备令牌：`dev_18198b705b0acb16`，明文存本机 `~/.ssh/agentos_device.json`
  - 防火墙：443/22/3389/ICMP（8899 已删）；按用户要求**不做 IP 白名单**，靠令牌鉴权
- **架构修正**：v1 的"电脑"= **宿主机 `:0` 桌面**（用户要求），不是 Docker 容器；容器留给后续隔离型任务

### 代码层

- 事件唯一写入口 `app.events.emit()`；kind 闭集 37 种；必填字段校验；payload >8KB 直接拒绝。
- 幂等：`(kind, client_msg_id)` 唯一，重投返回同一条。
- `typing` 为瞬态事件：只广播、不落库。
- 游标 = 全局自增 `events.id`；`read(since=)` 返回升序；`snapshot()` 支持向前翻页。
- WSS：连接后先 `hello`，再按游标分帧补发 `replay=true`，然后同连接切实时；慢消费者丢帧并发 `resync`。
- 鉴权：设备令牌只存 sha256；`/v1/health` 公开，其余 Bearer。
- 测试 37 条全绿（`pytest tests/test_events_v1.py tests/test_stream_v1.py tests/test_bots_v1.py tests/test_screen_v1.py`）。

### 已上线接口（服务端实测通过）- `GET /v1/health`（公开）
- `GET /v1/events?since=`（Bearer）
- `GET /v1/sessions/{id}/snapshot`（Bearer）
- `WS /v1/stream?since=&token=`（事件流，先 backlog 再实时）
- `GET /v1/computer`、`GET /v1/computer/frame.jpg`、`WS /v1/computer/screen`（桌面预览）
- `GET/POST /v1/bots`、`GET/POST /v1/sessions`、`POST/GET /v1/sessions/{id}/messages`
- 实测：桌面帧 1280×720 JPEG 可拉取；中文消息幂等存取正常；WS 经 nginx 返回 101
- **v1.1（2026-10-27）补齐交互**：
  - 新建 Bot（侧栏 ＋）、搜索（Ctrl/Cmd+K，搜 Bot/会话/消息/文件 + 快捷操作）
  - 连接应用（4 个内置 + 开关；真正 OAuth/MCP 后续）
  - 设置（通用/电脑：模型、每日 token、默认仅观看、预览帧率、唤醒/休眠）
  - 保险库（Fernet 加密存储；列表只回掩码；查看接口只给人；模型无工具可读）
  - 电脑全屏 + 接管（仅观看/取得控制权，点击经 xdotool 注入真实鼠标，键盘输入框 + Enter/Esc 快捷键）
  - 新接口：`/v1/search`、`/v1/apps`、`/v1/settings`、`/v1/vault*`、`POST /v1/computer/{wake,sleep,input}`
  - 测试：57 条 v1 用例全绿（`pytest tests/test_*_v1.py`）
- **v1.2（2026-10-27）**：
  - **设备配对**：`POST /v1/pairing/codes`（已登录设备生成 6 位一次性码，5 分钟）→ `POST /v1/pairing/claim`（新设备无令牌兑换长期令牌）；线上实测通过
  - **模型切换**：`GET /v1/models`、`PATCH /v1/bots/{id}`；支持全局默认 + 每 Bot 覆盖，loop 真正把 model 传给模型调用
  - **删除/清理**：`DELETE /v1/sessions/{id}`、`DELETE /v1/bots/{id}`（软删）；侧栏右键菜单
  - **UI**：聊天区滚动修复（flex min-height）、消息 Markdown 渲染、图标缩小、账号菜单加"打开项目工作台"（iframe `/legacy`）、配对码生成面板
  - **Agent 提示词**：Markdown 输出规范、主动给下一步、歧义时先执行并写假设
  - 测试：**79 条 v1 用例全绿**
- **v1.3（2026-10-27）**：
  - **工作台免登录（SSO）**：`POST /v1/legacy-sso/ticket`（设备令牌）→ 一次性票 → `/v1/legacy-sso/exchange` → 工作台签名 token；`/app/` 启动时自动换票，**不再二次登录**
  - **整窗切换**：`赛博指挥舱` 从弹框改为**整个应用切换**到 `/app/`（保留浮动"返回对话"）；账户菜单/侧栏/右键均可进入
  - 测试：**85 条 v1 用例全绿**
- **v1.4（2026-10-27）**：本地电脑能力（Grok Bot 式）
  - **exe 出站本地桥**：`WS /v1/device`（设备令牌）登记能力 `exec / fs / info`，断线 5 秒自动重连
  - **本地工具**：`local_info / local_list / local_read / local_write / local_exec`，经 `app/local_bridge.py` → exe Rust 执行
  - **逐次审批**：默认每次调用弹「本地电脑操作请求」，允许只对这一次生效；180 秒未决自动撤回卡片（`approval.decided: expired`）；刷新界面不再回放历史审批（`GET /v1/local/approvals` + 启动游标）
  - **设置里可见状态**：设置 → 通用 →「本地桥已连接 N 台 / 未连接」；`local_enabled`（默认关）、`local_approval`（默认开）
  - **真机验证**：exe 内点「允许这一次」后读到 `DESKTOP-PCZ6S91 / juling.wu.ext / C:\Users\Public`
  - **exe 修复**：手动建窗补 `--ignore-certificate-errors`（否则自签证书下静默退回登录页）；登录页增加"连不上服务器 + 重试连接"提示
  - **dsh-im 接入引导**：设置新增「IM 机器人」页（12 渠道、安装命令可复制、指南/GitHub 链接，`GET /v1/integrations`）；「连接应用」保留为未来 MCP 管理入口，不再放假开关
  - 测试：**744 条全量用例通过**（其中 v1 95 条，本地桥 10 条）
- `GET /v1/bots/{id}/details`（今日用量）、`GET /v1/bots/{id}/library`、`GET /v1/workspace`、`GET /v1/workspace/raw`
- **网页版界面已上线：`https://203.0.113.10/ui/`**（三栏：Bot/会话、聊天、详情·资料库·电脑）
  - 2026-10-27：整体改版为**暗色赛博风**；根路径 `/` 302 → `/ui/`；新增居中登录页（服务器地址/设备令牌，连接前先探活验令牌）；旧工作台移到 `/legacy`（登录后管理入口，不在登录页暴露）
  - 截图：`build/shots/login_cyber.png`（登录页）、`build/shots/app_ui2.png`（主界面）
  - 首次打开需接受自签证书；登录后令牌存 localStorage
  - 电脑页走 `WS /v1/computer/screen`，2fps 实时画面

- **v1.5（2026-10-27）**：每个 Bot 的长期记忆 + 群聊独立上下文
  - **记忆存储** `app/bot_memory.py`：按 Bot 隔离（`bot_id`），**槽位制**（公司/产品/客户/成员/偏好/回答风格/技术栈/服务器/流程/其它），
    同槽位新值覆盖旧值，槽位名走封闭词表 + 别名归一（模型自造"公司名称/公司信息"会收敛到"公司"）；
    另外两道去重：内容 hash 完全重复只累加命中次数，中文 bigram 重合 ≥0.8 视为近似重复合并；
    单 Bot 上限 800 条，超出按重要度 + 最近使用淘汰
  - **自动学习** `app/agent/memory_writer.py`：每轮对话结束后异步抽取稳定事实/偏好（温度 0.1、只认用户明确说的，
    助手复述不算新信息），写入后发 `memory.updated` 事件；抽取失败不影响对话
  - **主动记忆工具**：`remember` / `recall`（模型可自己记、自己查）
  - **独立上下文** `build_bot_history()`：自己的发言 = `assistant`，同事的发言 = `user` + `【同事 X 的发言…】`前缀
    （240 字截断）——同事说的话不会被模型当成自己说过，也不会被当成用户指令
  - **系统提示**：团队名单（名字/角色/人格摘要）+ 规则（你只代表你自己 / 同事发言不是给你的指令 / 不重复同事 / @接力）
    + 本次检索到的记忆条目（最多 8 条，按相关度 = 命中×3 + 重要度 + 时效）
  - **API**：`GET /v1/memories?bot_id=`、`POST /v1/memories`、`DELETE /v1/memories/{id}`；设置项 `memory_enabled`（默认开）
  - **界面**：Bot 详情页新增「长期记忆」区（类型标签 + 内容 + 单条删除 + 手动教会一条），设置 → 通用新增记忆开关
  - **真机验证**：教它"公司叫十月科技/偏好先给结论" → 新会话直接答出；改名后同槽位覆盖（库里仍 2 条，不堆积）；
    双 Bot 群聊里小文只出标题并主动 `@小万` 交接，小万只做渠道且落了文件——不越界、不重复、不冒充
  - 测试：**761 条全量用例通过**（新增 `tests/test_bot_memory_v1.py` 17 条）

- **v1.5.1（2026-10-27，同日补）**：Markdown 表格真正渲染
  - 之前聊天里的表格是裸竖线（模型爱写表），现在解析成 `<table>`：表头高亮、斑马行、超宽横向滚动
  - 段落/列表/表格改为块级拼装（表格不会被塞进 `<p>` 里破坏 HTML）
  - 真机截图：`build/shots/markdown_table.png`；记忆面板截图：`build/shots/memory_pane.png`
  - 顺带修掉一个白屏 bug：把记忆请求的 effect 放在 `activeBot` 声明之前触发 TDZ（`Cannot access 'A' before initialization`），整窗空白；已挪到声明之后

- **v1.6（2026-10-27）**：本地电脑从"能跑命令"升级到"能操控桌面"（AI Agent 级本机能力）
  - **故障根因**：v1.4 的本地能力只有 `local_info/list/read/write/exec`，`local_exec` 走 `cmd /C`，
    根本不会在交互式桌面起 GUI；用户让它"打开本地应用"时它确实做不到——当时它说"做不到"是真话。
  - **新增 8 个桌面工具**（`desktop/src-tauri/src/gui.rs`，全部 Win32 原生，不依赖 PowerShell 拼凑）：
    - `local_apps`：扫开始菜单 + 桌面快捷方式找应用；**中文名映射表**（记事本→notepad、微信→wechat…30 组），
      解决"快捷方式叫 Notepad.lnk、用户说记事本"的错配
    - `local_open`：`ShellExecuteW("open")` 真正落到当前桌面会话；启动后回报前台窗口，不是"发出去就算成功"
    - `local_windows`：`EnumWindows` 列可见窗口（标题/进程/是否前台/screen 尺寸）
    - `local_focus`：抢前台三级兜底（`AllowSetForegroundWindow` + 伪造 ALT 放开前台锁 + `AttachThreadInput` →
      `SwitchToThisWindow` → TOPMOST 抖动），并**校验 `GetForegroundWindow()` 真拿到了才返回成功**
    - `local_type`：`SendInput` + `KEYEVENTF_UNICODE` 逐字输入（中文可用），带 `title` 参数时**先聚焦、聚焦失败就不输入**
    - `local_keys`：组合键（ctrl+s / alt+tab / win+r / f5…），同样支持 `title` 定位
    - `local_click`：绝对坐标点击（屏幕尺寸校验，防越界）
    - `local_close`：对标题匹配窗口发 `WM_CLOSE`，走应用正常退出流程，不强杀进程
  - **安全**：全部经 `local_approval` 逐次审批；审批卡按风险分级（读=low / 启动·切窗=medium / 写文件·执行命令·键盘·点击=high），
    动作名改成中文人话（"在你电脑上打开应用/文件"），不再是裸工具名
  - **失败可诊断**：exe 失败时把完整 payload（`exit_code/stdout/stderr/窗口列表`）回传，不再只回一个 `failed`
  - **反编造**：系统提示加规则——"工具结果才算数""没调用过就不能说已打开/已输入""本机信息以本次 `local_info/local_windows` 返回为准，
    不拿历史或测试数据当事实"（起因：模型在一次失败后编造了 `FAKE-DESKTOP/tester` 这种假结果）
  - **真机验证**（`ses_b224b4c112b6a2a6`）：
    `local_apps(记事本)`→3 条命中（Notepad / Notepad++）→ `local_open(notepad)` 启动成功 →
    `local_focus(记事本)` 拿到前台（`foreground_now: 无标题 - 记事本`）→
    `local_type(text=十月智能 本地桌面可控 20261027, title=记事本)` 输入 20 字 →
    窗口标题变 `*无标题 - 记事本`（修改标记）+ 状态栏 `第 1 行, 第 20 列`；截图 `build/shots/local_notepad3.png`
  - **踩坑记录（写给未来的自己）**：exe 必须用 `npx tauri build --no-bundle` 构建。
    直接 `cargo build --release` 会用上一次的 `dist-tauri` 静态资源、甚至嵌不进去，启动后 WebView 报
    `tauri.localhost 拒绝连接`（表现为白屏/连不上服务器），排查半天其实是打包路径问题。
  - 测试：**765 条全量用例通过**（本地桥新增 4 条：GUI 工具注册、风险分级、参数透传、开关关闭时拒绝）

- **v1.6.1（2026-10-27，同日补）**：真机连跑三轮暴露的四个"假成功"修掉
  - **步数上限 8 → 14**，并且**用尽时强制收尾**：以前工具用满就丢一句"（本轮没有产出内容）"，
    现在会用一轮不带工具的小结（做了什么 / 每步结果 / 还差什么），不会静默断头
  - **`local_close` 回查**：以前 `PostMessageW(WM_CLOSE)` 发出去就报成功，但 Win11 记事本会弹"是否保存"，
    窗口还杵着。现在等 1.5 秒回查，窗口还在就返回 `still_open` + 处理建议（`alt+n` 不保存 / taskkill 兜底）
  - **`local_open` 等目标窗口出现**：改成按快捷方式名匹配进程、最多轮询 4 秒，返回 `target_window`
    （实测 `calc` → `CalculatorApp.exe` / 「计算器」/ pid），不再"睡 1.2 秒就说前台是别的程序"
  - **`local_apps` 名称修正**：显示名用 `file_stem`（`Notepad.lnk` → `Notepad`，不再是 `Notepad.`）
  - **故意被模型抓现行的一次**：它发现 `local_close` 报 ok 但窗口仍在，先如实说明再用 taskkill 收尾——
    这正是 v1.6 加"工具结果才算数"规则想要的行为

- **v1.7（2026-10-27）**：流式回复打通（路线图第 3 项收尾）
  - **模型层** `llm.chat_stream()`：SSE 逐块解析，`tool_calls` 按 `index` 累积碎片参数，
    `stream_options.include_usage` 拿真实 token 进账本（流式以前会漏记成本）
  - **事件层**：新增 `message.delta`（**瞬态**，id=0 不落库）——一篇 900 字的回答如果逐字入库，
    事件表会被淹没、WS backlog 也会被撑爆，所以增量只活在线路里，最终答案才落 `message.bot`
  - **节流**：循环里按 80ms / 48 字聚合再发，避免一 token 一帧
  - **回落策略（硬规则）**：
    * 流式在第一个字之前失败 → 静默退回非流式 `chat()`，用户无感；
    * 已吐出内容后失败 → **保留半截并作为本轮结果落库**，不重跑（重跑会让用户看到内容闪现又消失）
  - **桌面端**：`message.delta` 累加成实时气泡（Markdown 实时渲染 + 呼吸光标），
    `message.bot` / `turn.ended` 到达即清除草稿，以持久化消息为准
  - **顺带修掉两个真 bug**：
    1. WS `send_events` 拿 `items[-1]["id"]` 推进游标 —— 瞬态事件 id=0 会把游标打回 0，
       下次重连整库重放；改成只取 `max(id)` 且忽略 0
    2. 重连 `replay=true` 的历史补发把**所有历史 `tool.call` 灌进"本轮过程"**（实测堆了 30 行，
       把正文挤出视口）；现在重放事件不进过程，且新一轮 `turn.started` 清空过程
  - **真机验证**：
    * 服务端 WS 观察者（`build/watch_stream.py`）在一次回答里抓到 **21 帧 `message.delta`**，
      随后才是 `message.bot` / `turn.ended`
    * 桌面端页面内探针：12 个采样，气泡字符数 28 → 40 → 76 → … → 343 递增（真在打，不是一次性塞）
    * 900 字长文流式进行中截图 `build/shots/streaming_live.png`（含 Markdown 小标题与光标）
  - 测试：**774 条全量通过**（新增 `tests/test_streaming_v1.py` 9 条：SSE 内容/用量、tool_calls 碎片拼接、
    HTTP 错误、瞬态不入库、流式→落库顺序、首个字前失败回落、半截保留、游标不被瞬态打回）


- **v1.8（2026-10-27）**：例行任务 + 技能（路线图第 4 项，语音按用户要求跳过）
  - **例行任务** `app/routines.py`：Bot 按计划自己干活
    * 调度表达式只留两种：`interval:30m / interval:2h` 与 `daily:09:00`。
      通用 cron 要写解析器、处理一堆边界，收益不匹配——"每小时巡检 / 每天早报"覆盖真实需求
    * 执行**不伪造 `message.user`**：先发 `message.system`（「【例行任务】标题」，用户看得见），
      再用 `task_hint` 起一轮。伪造用户发言会污染上下文，还会被记忆抽取当成用户说的
    * 后台调度线程 20 秒扫一次，每个任务加锁防重入；pytest 下用 `PYTEST_CURRENT_TEST` 硬门禁，
      免得测试互相污染
    * 单条任务失败只记 `last_status=failed` + `error` 事件，不能把调度器带死
  - **技能** `app/skills.py`：把"怎么做事"沉淀成可复用指令包
    * 结构 = 名字 + 触发描述 + 指令正文（`{input}` 占位）。归属：`bot_id=null` 全局、否则私有；
      同名时**私有覆盖全局**（团队默认流程可被个人流程替换），所以查重要按归属精确判，不能一刀切
    * 取用**不额外起模型调用**：`use_skill` 把正文塞回上下文，模型照做——省钱、可控、方便人评审
  - **模型侧新工具**：`list_skills` / `use_skill(name, input)` / `list_routines`；
    系统提示加第 10 条："接到任务先用 list_skills 看有没有匹配技能，有就照做，别自己另起一套"
  - **API**：`GET/POST/PATCH/DELETE /v1/routines`、`POST /v1/routines/{id}/run`、
    `GET /v1/routines/schedule/preview`；`GET/POST/PATCH/DELETE /v1/skills`
  - **界面**：Bot 详情页新增「例行任务」（任务名 + 计划 + 内容 + 新建；列表带下次运行/跑过次数/
    立即执行/停用/删除）和「技能」（名称+用途+步骤+全局开关；列表带归属与使用次数）
  - **真机验证**：
    * 建了 `唤醒自检`（每 30 分钟）→ 点「立即执行」→ 事件链
      `routine.triggered → message.system「【例行任务】唤醒自检」→ message.bot → turn.ended → routine.finished`，
      `run_count` 1、`next_run_at` 顺延到 23:14
    * 建全局技能「周报三段式」→ 对话里说"写本周周报，先看看有没有技能"→ 模型自己
      `list_skills → use_skill(input=…) → list_routines`，按三段式输出，并如实说明数据来自哪次工具调用；
      `use_count` 记为 1
    * 截图：`build/shots/routines_skills2.png`
  - **顺带修**：`ensure()` 原来用进程级 `_SCHEMA_READY` 缓存建表结果——测试每个用例一个临时库时，
    第二个库就没有表（`no such table: routines`）。改成按 `db.db_path()` 缓存
  - 测试：**789 条全量通过**（新增 `tests/test_routines_skills_v1.py` 15 条）


- **v1.9（2026-10-28）**：三块硬骨头一起啃（用户要求 1/2/3 全做，语音跳过）
  - **① 安心债：真实 TLS 证书链**（详见 `docs/design/TLS.md`）
    * 没有域名只有 IP，所以自建 CA：RSA4096/10 年根 + RSA2048/397 天叶证书，
      SAN 含 `203.0.113.10 / 127.0.0.1 / localhost`；`deploy/tls/gen_ca_and_cert.sh` 幂等，
      支持 `--force/--new-ca/--revoke/--gen-crl/--print`
    * **踩到 Windows 的硬要求**：只装根证书不够 —— Schannel（curl/WebView2 同底）建链时强制吊销检查，
      私有 CA 没有 CRL 分发点直接报 `CRYPT_E_NO_REVOCATION_CHECK (0x80092012)`（curl exit 35）。
      所以方案 = 2 级链 **+ 叶证书带 CDP + nginx 在 443 发布 DER CRL**（该机器 80 端口的公网入向
      被云安全组挡着，CDP 只能用 https）。做过一次无破坏性吊销演练
    * **客户端去掉 `--ignore-certificate-errors`**（`desktop/src-tauri/src/main.rs`）：正常启动不带任何
      跳过证书校验的参数，只保留 `AGENTOS_DEBUG_PORT=1` 时开 CDP、`AGENTOS_IGNORE_CERT=1` 作排障兜底；
      真机验证：WebView2 正常加载（改前必须靠忽略参数才能起）
    * 安装：`scripts/install_ca_windows.ps1`（CurrentUser\Root，打印指纹，`-Remove` 可回滚，`-Verify` 自检）
    * 验证：`curl.exe` 不带 `-k` → 200；卸载 CA 后立刻 exit 60（反证是这张 CA 在起作用）；
      服务器 `verify_tls.sh` 10 项全过
  - **② 审批可视化 + 目录白名单**（`app/local_policy.py`）
    * 审批卡不再只显示 `path=..., content=...` 截断串：动作名说人话 + **风险徽标**（低/中/高）+
      **结构化预览**（写入内容片段 / 命令原文 / 输入的字符数与目标窗口）
    * 目录白名单 `local_allow_dirs`（设置 → 通用）：配置后越界**直接拒绝、连审批都不弹**，
      策略只收在服务端（客户端可被绕过）；留空 = 不限制（保持旧行为）
    * 路径归一化容忍粘贴错误（正斜杠/双反斜杠/外层引号/尾分隔符）—— 真踩过：设置里存成
      `C:\\Users\\...` 时白名单把自己的目录也拦了
  - **③ W4 进程 supervisor**（`app/processes.py` + `app/api_v1/processes.py`）
    * `processes` 表沿用契约 schema（`status/exited_at/pgid` 那套），增量补列 `state/name/session_id/created_at`，
      双写 `state↔status`、`ended_at↔exited_at`，老行词表映射（`stopped→killed`、`lost→orphaned`）
    * POSIX `start_new_session`（setsid 等价）/ Windows `CREATE_NEW_PROCESS_GROUP`；
      stdout/stderr 落 `<workspace>/logs/<id>.log`（不用 PIPE，避免塞满管道卡死）
    * API：列表/创建/详情/stop/`log?tail=N`；`recover_interrupted` 在启动时把遗留 running 标 lost
    * 真机验证：启动 `python3 -c print/sleep` → running/pid 可见 → 日志读到 `tick`/`done` →
      tick 后 state=exited、exit_code=0、`process.started` + `process.exited` 事件齐全
  - **④ 截图 + 视觉闭环**（`local_screenshot`）
    * exe 侧：GDI `BitBlt`+`GetDIBits` 抓屏 → 面积平均缩放到 1600 宽 → PNG 编码；
      窗口截图走 `PrintWindow(PW_RENDERFULLCONTENT)`，拿不到内容回退屏幕矩形并如实标 `captured_via`
    * 传输：二进制走 WS `{"t":"artifact"}`（base64），**不进事件**（事件 8KB 上限）；
      服务端 `app/artifacts.py` 落 `<workspace>/artifacts/art_<16hex>.png` + `.meta.json`，
      只发 `artifact.attached` 引用事件，`GET /v1/artifacts/{id}` 取二进制
    * 界面：聊天里出现图片气泡（Bearer fetch 成 blob、按 id 缓存、点击开整窗查看器、切会话回填历史）
    * 实测：全屏 1600x1000 **端到端 381ms**（抓屏+缩放 190ms、PNG 编码 186ms），
      1.0~1.6MB；tool_result 元数据仅 428~567 字符
    * **诚实边界**：模型看不到像素（DeepSeek 非多模态）。截图是给**人**看的 + 给后续视觉能力铺路；
      Bot 判断界面状态仍靠 `local_windows` 标题 + 用户描述
  - 测试：**820 条全量通过**（新增 `test_processes_v1.py` 17 条、`test_local_guard_v1.py` 14 条）
  - 截图：`build/shots/approval_card.png`（高风险徽标 + 内容预览）


## 下一次开工点

1. **语音**（用户要求先跳过）：语音输入（转写）+ 语音播报，接在流式回复之后最自然。
2. **W9 手机/推送**：移动端只读视图 + 推送通知（审批/例行任务失败）。
3. **Agent 侧进程工具**：`proc_start/proc_ps/proc_tail/proc_kill`（supervisor 已有 API，模型还没有工具）；
   顺带补 `max_runtime_s` 超时强杀、日志轮转、高危命令审批。
4. **视觉（取决于模型）**：等有多模态模型再接 `local_screenshot` → 视觉判断；否则维持"截图给人看"。
5. **证书运维**：叶证书 397 天、CRL 365 天，需按 `TLS.md §10` 挂 cron 自动续签；CA 10 年。
