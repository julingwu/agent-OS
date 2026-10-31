# AgentOS 双轨架构演进与全功能交付记录 (2026-10-31)

## 1. 架构升级总览 (Architecture Overview)

针对 AgentOS 自我修改能力、稳定性、Windows 原生体验以及 Homelab 生态的深度集成，本阶段完成了以下核心升级：

### 1.1 双轨守护与零停机平滑切换 (Green-Red Dual-Track Architecture)
- **无头看门狗 (`supervisor.py`)**：运行在 `http://127.0.0.1:8077`，统一对外提供反向代理、静态资源托管与 WebSocket 长连接透传。
- **绿/红双实例热备**：
  - **绿进程 (Green Track)**: 默认绑定端口 `8081`。
  - **红进程 (Red Track)**: 默认绑定端口 `8082`。
- **零停机平滑热切流程**：
  1. 代码修改完成后，通过 `/v1/supervisor/restart_standby` 让备用轨加载新代码；
  2. 备用轨通过健康检查与自检后，调用 `/v1/supervisor/switch` 瞬间接管对外请求流量；
  3. 主备实例共享同一 SQLite WAL 数据库 (`data/oa.db`)，会话状态、事件历史与长短期记忆完全无感延续。
- **切换感知**：
  - 前端顶部胶囊实时显示当前主控轨道（🟢 绿轨 / 🔴 红轨）与端口；
  - 切换完成后在会话消息流与置顶条浮出提示，告知用户最新生效功能。

### 1.2 Magpie Homelab 网关模型专属接入与动态推理强度
- **网关专属接入**：连接本地私有网关 `http://192.168.0.3:3425/v1`，剔除前端原写死的冗余内置模型列表，实现纯粹的模型呈现。
- **模型推理强度动态等级匹配**：
  - `deepseek-chat` / `deepseek-reasoner`: 动态匹配 `none` (关闭思考), `low` (轻量推理), `high` (深度思考), `max` (极限算力)。
  - `gpt-6.1-turbo` / `codex/gpt-6.1-ext`: 动态匹配 `low`, `medium`, `high`, `xhigh`, `max`, `ultra` 6 档深度。
- **交互位置优化**：将模型选择和推理强度控制从右侧面板迁移至消息输入框正下方，符合现代主流 AI 聊天工具的操作习惯。

### 1.3 实时交互增强：剪贴板图片、执行中动态转向与 0ms 乐观上屏
- **直接粘贴截图与拖拽上传**：`handlePaste` 拦截剪贴板文件直接存入工作区并引用到输入框，支持多格式拖拽。
- **0ms 乐观上屏**：用户消息发送瞬间即在界面生成消息气泡，消除网络往返等待感。
- **在途中实时转向 (`In-Flight Steering`)**：执行长耗时任务时输入框自动切换为“转向 / 追加 ↪”，Agent 循环在每一步 step 中实时消费用户最新注入意图，无缝调整执行方向。

### 1.4 运行透明度：实时动向探测器 (Live Activity Monitor)
- **电脑面板下方与独立“运行”Tab**：将后台的黑盒转圈升级为可视化流水线，实时输出当前调用的工具名、参数摘要、执行耗时与终端输出。
- **智能滚动跟随**：支持到底自动跟随最新日志，用户向上滚动翻阅时不打断。
- **Agent 自我架构感知**：在系统提示词中注入自身所处双轨机制与重载方法，使 Agent 具备清晰的自我修改与自救意识。

### 1.5 关键 Bug 修复与稳健性
- **TDZ 暂时性死区消除**：重构 `desktop/src/App.jsx` 状态声明顺序，解决 F5 刷新报 `Cannot access 'W' before initialization` 的问题。
- **事件 128KB 载荷扩展与入参截断保护**：解决长代码写入或超长返回导致的 `payload too large for tool.call: >8192B` 中断。
- **屏幕流原生适配**：Windows 原生分辨率感知与 Pillow 截屏，彻底解决 Linux ffmpeg 缺失导致的 `[WinError 2]` 报错。
- **长期记忆 409 降级与前端 ErrorBoundary**：防止任何单点异常引起整个窗口闪退。

---

## 2. 核心修改文件清单

```text
├── supervisor.py                      # 双轨看门狗核心，反向代理与热切换服务
├── app/
│   ├── agent/
│   │   ├── loop.py                    # 实时转向检测、载荷安全截断、自我架构感知提示词
│   │   └── ext_tools.py               # 工具扩展
│   ├── events.py                      # 事件总线载荷扩容 (128KB)
│   ├── computer/screen.py             # Windows 原生屏幕推流与截图
│   ├── bot_memory.py                  # 时区兼容性修复
│   └── api_v1/bot_memory.py           # 记忆判重 409 平滑降级
├── desktop/
│   ├── src/
│   │   ├── App.jsx                    # 状态置顶声明、运行面板、剪贴板粘贴、双轨胶囊、动态推理强度
│   │   ├── main.jsx                   # ErrorBoundary 全局防护
│   │   ├── theme.js                   # 主题管理
│   │   └── styles.css                 # 16:9 比例适配、浅色主题抗黑修正、运行面板样式
│   └── vite.config.js                 # 启用 sourcemap: true
├── ui_dist/                           # 生产静态资源构建产物
└── docs/
    ├── ISSUES_BACKLOG.md              # 汇总待办与修复状态 (Issue #01 - #09)
    └── CHANGELOG_20261031.md          # 本次演进全量技术说明
```
