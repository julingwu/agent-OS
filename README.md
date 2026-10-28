# OA Agent · M0 — Agent OS

一句话下需求 → 拆阶段与 WBS → 生成可执行判据 → 双内核执行 → L0/L1/L2 分层验收 → 审计回放 → 经验沉淀。
底座是 FastAPI + SQLite（WAL 模式），前端是 React + Vite（提供 Tauri 2 跨平台桌面端与移动端支持）。

当前版本 **v5.4.3 / v1.0 (Desktop & Mobile)**：**822 条自动化测试全量通过**。

---

## 核心新特性 (v5.4.3 / v1.0)

### 1. 桌面端与移动端双客户端（Tauri 2）
* **Windows 桌面端 (`agentos-desktop.exe`)**：原生内嵌 Win32 本地桥接，具备前台窗口枚举聚焦、低延迟 GDI 全屏截取、隔离进程组执行与日志管道。
* **Android 原生客户端 (APK)**：
  * 内置自建 CA 根证书与网络安全策略，无需在手机系统设置里手动导入证书；
  * 移动端抽屉式自适应布局（`☰` 会话列表抽屉 + `⚙` 助手详情与技能抽屉），适配虚拟键盘与底部安全区（`safe-area`）。
* **多端任务互通**：任务与消息状态由云端统一调度，电脑下达的任务手机实时同步，手机下达的任务电脑桌面无缝继续。

### 2. 角色库（43 行业角色）与技能体系全面联动
* **职责即角色**：新建助手时可直接选择角色模版（技术研发、电商运营、市场内容、行政财务、战略高管等），自动带入专业 Prompt 人设与英文标识。
* **基础技能自动落库**：创建助手时自动将其角色预置的基础技能植入 `bot_skills` 表。
* **技能快速借调与赋予**：助手详情面板支持一键删除多余技能，并提供「💡 角色推荐技能 / 快速赋予」面板，可一键赋予本角色专属技能或从其他 42 个角色模版中跨角色借调技能。
* **基础核心技能**：预置本地通道自检、系统健康自检、桌面窗口操控、周报三段式、第一性原理决策分析等系统级能力。

### 3. 企业级安全护栏与真实证书链（非"跳过校验"）
* **2 级私有 CA 证书链**：RSA 4096 根证书 + RSA 2048 / 397 天叶证书，带 CDP 吊销分发点与 Nginx DER CRL 发布，通过 Windows Schannel 与 curl 严格校验，彻底移除 `--ignore-certificate-errors` 隐患。
* **审批卡与目录白名单**：高风险写入/执行操作弹出带风险徽标、人话摘要与内容预览的审批卡；配置 `local_allow_dirs` 目录白名单后越界直接静默阻断，杜绝路径穿越。
* **W4 进程 Supervisor**：支持 POSIX `setsid` / Windows `CREATE_NEW_PROCESS_GROUP`，日志独立落盘，服务重启后自动识别并标记遗留的孤儿进程。

### 4. 架构解耦与自备主机（BYOH）私有化
* 服务端主机并非全局硬编码绑定，客户端支持自由输入任何服务器地址连接，实现“各人用各人的服务器，数据物理级隔离”。
* 提供云端 GitHub Actions 自动化打包流水线（`.github/workflows/build-android.yml`），自动内嵌证书并生成开箱即用的 Android 安装包。

---

## 界面概览

> 截图取自桌面端与赛博指挥舱。

| 赛博指挥舱：阶段流水线一览 | 总装流水线：单项目阶段推进 |
|---|---|
| <img src="docs/screenshots/v5/cyberdeck.png" width="620"> | <img src="docs/screenshots/v5/projects.png" width="620"> |

| 任务中心：任务 / 判据 / 运行记录 | FinOps：token 折算人民币与预算池 |
|---|---|
| <img src="docs/screenshots/v5/tasks.png" width="620"> | <img src="docs/screenshots/v5/finops.png" width="620"> |

| 组织经验治理：待审 / 生效 / 已驳回 | 评测中心：离线回归与判据覆盖 |
|---|---|
| <img src="docs/screenshots/v5/experience.png" width="620"> | <img src="docs/screenshots/v5/evals.png" width="620"> |

---

## 架构核心机制

- **分层验收**：L0 规则自动判定 / L1 人工签批（必须给 evidence_snippet）/ L2 沙箱脚本真实执行，三层证据全部落库，**模型不能自评通过**。
- **双内核执行**：`NativeKernel`（进程内工具调用，带真模型双模）与 ACP（JSON-RPC 子进程）可切换，`task_runs.runner` 记录真实内核。
- **租户与法人实体隔离**：项目、任务、运行、产物、经验、评测集按 `tenant_id` 物理过滤。
- **FinOps 双口径**：任务消耗（`task_runs.usage_tokens`）与阶段 overhead（立项/研讨/WBS/复盘等 `llm.complete` 调用）分列统计，阶段 80% 预算检查同时覆盖两者。
- **经验闭环**：驳回 / 共识 / 否决 / 复盘自动沉淀，人工审核转正，注入前做标记剥离防「套娃污染」。
- **可复现的稳定性**：后台线程绑定发起时的数据库并登记排空，测试之间不互相踩库。

其它设计参考：[TLS 规范与运维](docs/design/TLS.md) · [设计缺陷台账](DESIGN_GAPS.md) · [部署手册](DEPLOY.md) · [工具接入](docs/EXTENSIONS.md)

---

## 快速开始

### 1. 启动服务端

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
# 将上一步输出写入本机 .env 的 UI_SESSION_SECRET，再启动服务
.\.venv\Scripts\python.exe tools/auth_admin.py issue --name ceo-local --role ceo
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8077
```

### 2. 编译并启动桌面端

```powershell
cd desktop
npm install
npm run build:tauri
# 生成产物：desktop/src-tauri/target/release/agentos-desktop.exe
npx tauri build --no-bundle
```

### 3. 构建 Android 移动端 APK

代码已集成 GitHub Actions 自动化编译流：
1. 推送代码至 GitHub 仓库；
2. 打开 GitHub 仓库页面 -> **Actions** -> 选择 **Build Android APK (Tauri 2 Mobile)**；
3. 点击 **Run workflow**，构建完成后在 Artifacts 区域下载 `AgentOS-Android-APK` 安装包即可。

---

## 执行器对比

| RUNNER | 传输 | 预算闸 | 花费 |
|---|---|---|---|
| `mock`（默认） | 子进程 + JSONL | 开跑前整笔预扣 | 0 |
| `mock_acp` | JSON-RPC（mock 服务端） | 开跑前预扣 + 越线取消 | 0 |
| `dsh` | `dsh --profile headless --json -`，任务走 stdin | 开跑前整笔预扣（黑盒，看不到中途用量） | 真实 token |
| `acp` | `dsh --profile acp`，JSON-RPC 双向 | 开跑前预扣 + **每次调用后越线即 `session/cancel`** | 真实 token |

ACP 是"管钱"的那条路：`usage_update` 每调用一次推一次，外壳按 `baseline + 预留` 判定，越线立刻取消，本轮后续调用不再发生。

---

## 质量与自动化测试

```powershell
.\.venv\Scripts\pytest.exe -q
```

全量测试套件（含 89 个测试文件）：
- **822 passed**（包括桌面投射 API、护栏边界判定、路径穿越防御、角色与技能绑定等）。

---

## 目录结构

- `app/api_v1/` — v1 投影 API（Bot 视图、会话、设备接入、流式总线、角色库、技能）
- `app/agent/` — 循环 Agent 核心与工具调用链
- `app/computer/` — 电脑桌面操控核心抽象
- `app/local_bridge.py` & `app/local_policy.py` — 本地设备网关与操作白名单安全护栏
- `app/processes.py` — W4 进程管理器
- `desktop/` — Tauri 2 跨平台客户端代码（React 前端 + Rust 桌面/移动多目标内核）
- `deploy/tls/` — 自建 CA 根证书与 CRL 发布配置
- `.github/workflows/` — 云端 Android APK 自动化构建工作流
