# Codex CLI 接入

网页版助手详情已拆分“模型”和“推理强度”两个联动控件，切换模型清除旧强度，保存期间锁定控件。
CLI 等待期间每5秒发送处理时长事件，客户端轮询核对会话运行状态，避免重连错过结束事件后一直显示思考。
当前仍是完整结果回传，不宣称逐字流式显示。

Windows 桌面桥支持 HTTP 环回地址的明文 WebSocket，HTTPS 继续校验证书。
已安装微软官方 MSVC/Windows SDK，以 Release 模式和 `tauri/custom-protocol` 特性编译，
将两级选择器与连接修复一同嵌入桌面 exe。2026-10-29 已替换本机桌面版本，旧版本另存备份。
新 exe 已通过 HTTP WebSocket 握手和真实 `local_info` 往返测试，正式服务确认本地设备在线。

模型列表通过官方 CLI `app-server model/list` 动态读取（短缓存），将每个模型支持的推理
级别显示为明确组合，兼容已发布桌面客户端的模型下拉框。`codex-cli/<模型>#<级别>`
在调用时拆成 `--model` 与 `model_reasoning_effort`；不支持的组合在启动前拒绝。
例如 `codex-cli/gpt-6.1-sol#high`。目录可能来自CLI缓存，展示不等于账户额度/权限保证。
后端更新后，已打开的旧桌面客户端需要 Ctrl+R 重新加载模型目录。

聊天 Agent 的模型选择中增加 `codex-cli`。可选 `codex-cli/<模型 ID>` 指定账户可用的模型；
裸 `codex-cli` 使用官方 CLI 默认模型。本接入不替换旧项目的 DSH/ACP 执行内核。

在仓库根目录运行 `powershell -ExecutionPolicy Bypass -File tools/login_codex.ps1`，
在官方页面自行登录。专用登录目录是 `data/codex-cli`（已被 Git 忽略），不复用或修改
Codex 桌面应用登录。服务和登录脚本必须使用同一个 Windows 用户。
可用 `CODEX_CLI_BIN` 指定 CLI 可执行文件，`AGENTOS_CODEX_HOME` 自定义专用目录；
如更改目录，登录时也须使用相同 CODEX_HOME。

实现使用 `codex exec` 的结构化输出：每次请求传入已有会话和应用工具声明，返回文本或工具请求。
输出 schema 按工具名绑定参数对象，避免把复杂 Windows 命令二次编码成 JSON 字符串；
可选参数在传输时使用 null，分派前省略并按原工具 schema 校验，不把无效请求交给执行器。
工具继续由 Agent OS 原有分派、审批与事件记录处理。CLI 使用空临时工作目录、只读沙箱，
不加载用户配置和执行规则，关闭内置 shell、插件、子 Agent 和网络搜索；环境不转发 API 密钥。
返回未授权工具名、非对象参数或内置工具执行事件时拒绝本轮结果。

这是完整结果回传模式，当前不逐字流式显示；每一步独立启动 CLI、传入历史，存在额外启动开销。
使用 CLI 返回的 input/output tokens 记账，不将其解释为 DeepSeek 的实际人民币账单。
超时或登录/额度错误会明确报告，不自动切换至其他收费模型。

测试：`python -m pytest tests/test_codex_cli.py -q`。登录后的真实调用需要另外验证。

2026-10-29 本地执行修复：cmd 使用原始命令行传参，避免 CRT 转义破坏引号；
运行时并行排空 stdout/stderr，仅保留有限输出，避免大量输出阻塞。
本地命令最多运行150秒，桥的等待时间比命令期限多10秒；超时终止命令进程树。
已用新版 exe 验证带引号命令、双管道大输出、超时后继续接收调用；
正式服务已完成真实 Codex → 单次审批 → 桌面 Python 命令 → 结果回传 → 正常结束测试。
