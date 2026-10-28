<div align="center">

# 🤖 AgentOS (OA Agent) · 次世代企业级自主智能体协作操作系统

**多端全双工协同 · 43 类行业岗位角色 · 动态技能组合 · 桌面原生操控流 · 金融级安全信道 · 自备主机私有化**

<p align="center">
  <img src="https://img.shields.io/badge/Version-v5.4.3%20%7C%20v1.0-blue.svg?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/Automated%20Tests-823%20Passed%20(100%25)-success.svg?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Android%20%7C%20Web-orange.svg?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/Roles%20Library-43%20Standardized%20Personas-purple.svg?style=flat-square" alt="Roles">
  <img src="https://img.shields.io/badge/Security-TLS%201.3%20%2B%20CDP%2FCRL%20%2B%20Hard%20Guardrails-red.svg?style=flat-square" alt="Security">
  <img src="https://img.shields.io/badge/Architecture-Tauri%202%20%2B%20FastAPI%20%2B%20SQLite%20WAL-informational.svg?style=flat-square" alt="Architecture">
</p>

<p align="center">
  一句话下达业务需求 ➔ 43 岗 AI 助手协同 ➔ 实时接管操作电脑 ➔ 智能拆解 WBS 任务 ➔ L0/L1/L2 严苛验收 ➔ 组织经验自动沉淀
</p>

<p align="center">
  <a href="https://github.com/julingwu/agent-OS/raw/mainline/releases/AgentOS.exe">
    <img src="https://img.shields.io/badge/下载客户端-AgentOS.exe%20(6.16MB)-6366f1?style=for-the-badge&logo=windows&logoColor=white" alt="Download Windows Client" />
  </a>
  <a href="https://193.112.172.223/ui/AgentOS.exe">
    <img src="https://img.shields.io/badge/云端极速镜像-AgentOS.exe-10b981?style=for-the-badge&logo=cloud&logoColor=white" alt="Cloud Download Mirror" />
  </a>
</p>

---

</div>

## 📑 目录导航

- [✨ 双舱协同架构：主工作台与指挥舱](#-双舱协同架构主工作台与指挥舱)
- [🖼️ 界面全景与真实运行效果](#️-界面全景与真实运行效果)
  - [1. 核心主工作台（登录后主界面）](#1-核心主工作台登录后主界面)
  - [2. 赛博全息指挥舱（工程总装第二舱）](#2-赛博全息指挥舱工程总装第二舱)
  - [3. 43 类细分行业角色库 × 12 工业级特化技能](#3-43-类细分行业角色库--12-工业级特化技能)
- [🏛️ 系统架构全景拓扑](#️-系统架构全景拓扑)
- [🚀 六大核心技术支柱](#-六大核心技术支柱)
- [⚖️ 执行内核与 FinOps 预算管控](#️-执行内核与-finops-预算管控)
- [🏁 快速开始与访问指南](#-快速开始与访问指南)
- [🛡️ 质量保证与测试套件](#️-质量保证与测试套件)
- [📂 仓库工程结构](#-仓库工程结构)

---

## ✨ 双舱协同架构：主工作台与指挥舱

传统的 AI 框架要么是“单调的聊天框”，要么是“复杂的后台流水线看板”，割裂了人机对话协作与宏观工程交付。

**AgentOS** 采用独特的**双舱协同架构**，各司其职又无缝秒级跳转：

```mermaid
flowchart LR
    subgraph CoreWorkspace ["【第一主舱】AI 团队协同工作台 (默认主界面)"]
        Chat["💬 多助手多轮对话<br/>(小万、小财等 43 岗位)"]
        Stream["🖥️ 实时桌面操控推流<br/>(低延迟视频流 + 键鼠接管)"]
        RolesConfig["🎭 岗位角色与技能配置<br/>(自带基础技能 + 自定义扩充)"]
        Memory["🧠 长期记忆自学沉淀"]
    end

    subgraph CyberDeck ["【第二切换舱】赛博全息指挥舱 (总装发射台)"]
        Launch["🚀 CEO 战略指令发射台"]
        Pipelines["⚙️ 标准 9 阶段软件工程流水线"]
        Octo["🪐 OCTO 4 轮研讨会智能推演"]
        FinOpsDeck["💎 晶石吸纳演练与算力消耗"]
        MCPEx["🔌 万界 MCP 交易所"]
    end

    CoreWorkspace -- "左下角一键展开「赛博指挥舱」" --> CyberDeck
    CyberDeck -- "顶部/侧边栏一键「← 返回主工作台」" --> CoreWorkspace
```

1. **核心主工作台（登录后默认主界面 · 图 1）**：
   * **业务日常协作核心**：用户登录系统后进入的默认核心界面。
   * **多角色 AI 团队直聊**：左侧展示团队所有助手（如小万、小财、技术架构师、法务顾问），支持一对一专聊、多 Agent 群聊。
   * **右侧实时电脑视窗与控制（Computer-Use）**：原生推流电脑屏幕，随时查看 Agent 正在电脑上干什么，支持一键“取得控制权”直接操作。
   * **角色与技能自由定制**：新建助手时从 43 个行业角色预设挑选，自动联动职责与基础技能，详情面板可随时扩充或剔除技能。
2. **赛博全息指挥舱（工程总装第二舱 · 图 2）**：
   * **宏观项目总装中枢**：主工作台左下角点击【赛博指挥舱】即可瞬时展开。
   * **复杂软件工程流水线**：9 阶段工程流水线、4 轮研讨会推演、沙盘演练、MCP 工具市场与 FinOps 晶石开销。
   * **双向无缝切回**：指挥舱顶部常驻【← 返回 AI 团队主工作台】按钮，左侧“我的工作空间”随时一键回归对话。

---

## 🖼️ 界面全景与真实运行效果

### 1. 核心主工作台（登录后主界面）
> 43 岗 AI 助手团队、多轮上下文会话、右侧原生电脑操控流推流与接管、岗位技能自定义增删。

<div align="center">
  <img src="docs/screenshots/v5/workspace_main.png" width="920" alt="核心主工作台（登录后主界面）">
</div>

### 2. 赛博全息指挥舱（工程总装第二舱）
> CEO 战略指令发射台、9 阶段工程流水线、OCTO 研讨会推演、沙盘演练、万界 MCP 交易所。随时点击顶部 `← 返回对话` 切回主工作台。

<div align="center">
  <img src="docs/screenshots/v5/cyberdeck.png" width="920" alt="赛博全息指挥舱">
</div>

---

### 3. 43 类细分行业角色库 × 12 工业级特化技能

系统数据库内建 43 类标准化企业岗位人设，并出厂预植 12 项开箱即用的工业级执行流程技能（按需直接赋能助手）：

| 行业领域 | 代表性预设角色 (共 43 类) | 预置特化技能示例 (DEFAULT_GLOBAL_SKILLS) |
|---|---|---|
| **技术研发** | 技术架构师、后端全栈专家、前端体验工程师、DevOps 运维总监 | **代码质量与安全审计**、**SQL慢查询与索引优化**、**架构评审与边界设计**、**系统健康巡检与探活** |
| **项目与敏捷** | 敏捷项目经理、敏捷教练 (Scrum Master)、技术文档工程师 | **敏捷项目燃尽与风险复盘**、**周报三段式提炼**、**Git提交规范提炼** |
| **商业与决策** | 战略投资总监 (小财)、行业研究分析师、商业分析师 | **第一性原理决策推演**、**电商竞品差评与痛点挖掘** |
| **财税法合规** | 增值税合规顾问、企业法务合规官、劳动法顾问 | **增值税与财务合规审核**、**中英双向专业术语翻译** |
| **市场与运营** | 爆款内容操盘手 (小万)、社媒增长专家、新媒体文案 | **爆款文案多平台适配**、**竞品痛点提炼** |

---

## 🏛️ 系统架构全景拓扑

```mermaid
flowchart TB
    subgraph ClientLayer ["客户端层 (Client Layer - Tauri 2 Multi-Target)"]
        WinClient["🖥️ Windows 桌面端 (AgentOS.exe)<br/>(Tauri 2 原生封装 / GDI 抓屏 / 独立进程组)"]
        WebClient["🌐 现代浏览器 Web 端 (/ui/)<br/>(React 响应式单页应用 / 双舱秒级跳转)"]
        MobileClient["📱 Android / 鸿蒙移动端<br/>(原生 APK / 安全区与抽屉自适应)"]
    end

    subgraph SecurityLayer ["安全与合规网关 (Enterprise Security Gateway)"]
        TLS["🔒 双级自建 CA 证书链<br/>(RSA 4096 根 + 397天叶证书 + Nginx 443 DER CRL 吊销点)"]
        Guardrails["🛡️ 结构化护栏引擎<br/>(高危操作审批卡 + local_allow_dirs 路径白名单硬阻断)"]
        AuthRBAC["🔑 租户隔离与 RBAC<br/>(租户物理隔离 + CEO/主管/执行员多角色鉴权)"]
    end

    subgraph CoreEngine ["核心调度与执行引擎 (Core Agent Engine)"]
        FastAPI["⚡ 高性能调度中枢 (FastAPI)"]
        DB[(🗄️ SQLite WAL 引擎<br/>bot_skills / 43角色库 / 租户数据物理隔离)]
        Supervisor["⏱️ W4 进程 Supervisor<br/>(独立日志落盘 / 跨重启 recover_interrupted 自动愈合)"]
        DualKernel["⚙️ 执行内核切换器<br/>(NativeKernel 进程内直驱 vs ACP 子进程 JSON-RPC)"]
    end

    subgraph CapabilityLayer ["智能体角色与能力资产 (Personas & Capabilities)"]
        Roles["🎭 43 类行业岗位角色库<br/>(研/产/销/运/管/财/法 全矩阵预置人设)"]
        Skills["🧰 12 项工业级特化技能库<br/>(决策推演 / 代码审计 / 财税合规 / 周报复盘)"]
        MCPBridge["🔌 MCP 协议桥接<br/>(标准外部工具接入 / 多生态模型扩展)"]
    end

    WinClient --> TLS
    MobileClient --> TLS
    WebClient --> TLS
    TLS --> Guardrails --> AuthRBAC --> FastAPI

    FastAPI --> DB
    FastAPI --> Supervisor
    FastAPI --> DualKernel

    DualKernel --> Roles
    DualKernel --> Skills
    DualKernel --> MCPBridge
```

---

## 🚀 六大核心技术支柱

### 1. 核心工作台即主入口，多 Agent 智能协同
* **拒绝千篇一律的通用对话**：系统内置 43 个细分行业角色标准定义，登录即直达多 Agent 协作主界面。
* **岗位创建即赋能**：新建助手时挑选角色，系统自动为其初始化核心 Prompt、能力标签与专属基础技能。
* **自由扩展与跨角色赋能**：助手详情面板支持直观增删技能，提供本角色推荐技能库，并支持从其它角色中按需借调。

### 2. 桌面原生执行桥（Desktop Native Bridge）与实时视窗
* **实时桌面推流**：主工作台右侧直接挂载桌面画面，随时观察 Agent 执行操作；
* **Win32 深度硬件交互**：通过 Windows API 实现前台活动窗口枚举与聚焦、多屏坐标计算；
* **超轻量原生抓屏**：利用 Win32 GDI 配合纯 Rust `png` 无损压缩算法抓屏，381ms 极速生成，规避重量级依赖。

### 3. 去中心化自备主机（BYOH - Bring Your Own Host）
* **数据主权私有化**：服务端主机与平台账号物理解耦。企业与个人用户自备 Linux VPS 或本地服务器，数据主权完全私有。
* **一键单行脚本拉起**：在服务器终端执行一行 curl 命令即可完成环境准备与服务守护进程注册。
* **客户端多实例管理**：客户端内置动态服务器切换器与 Ping 延迟探活，一键在“官方节点”、“自备云机”与“局域网实例”间切换。

### 4. 企业级严格护栏与人机共治（Guardrails & HITL）
* **三层可信验收标准**：
  * **L0 规则自动判定**：语法校验、状态码检查、格式匹配；
  * **L1 人工签批强制留痕**：审批人必须填写 `evidence_snippet` 凭据，**严禁大模型自评通过**；
  * **L2 沙箱脚本真实执行**：在隔离沙箱中跑自动化判据脚本，真实退出码作为唯一通过证据。
* **结构化高危审批卡**：直观展示操作对象人话摘要、目标路径、增减字符量与变更原文预览，消灭“盲批”隐患。
* **目录白名单硬阻断**：服务端全局配置 `local_allow_dirs`，越界路径静默拦截拒绝，不给任何越权穿透机会。

### 5. 全双工跨端协同（Windows Desktop + Web + Android Mobile）
* **Tauri 2 统一架构**：桌面与移动端共享同一套响应式 React 前端，桌面端编译为轻量 Win32 原生可执行文件，移动端直接输出原生 Android APK。
* **移动端抽屉自适应交互**：手机端内置顶部导航、双向侧滑抽屉，全面适配鸿蒙与 Android 主流机型分辨率与 Safe-Area 避让。

### 6. 金融级真证书安全链（Enterprise TLS / CDP / CRL 吊销）
* **拒绝任何不安全的妥协**：客户端彻底移除 `--ignore-certificate-errors` 启动参数。
* **2 级自建私有 CA 体系**：RSA 4096 位根证书（有效期 10 年）+ RSA 2048 位叶证书（有效期 397 天），SAN 覆盖全内网与公网 IP。
* **适配 Windows Schannel 强吊销检查**：叶证书内嵌 CDP（CRL 分发点），配合 Nginx 443 端口发布 DER 格式 CRL 吊销列表，经真实吊销演练验证，完全通过 Schannel 与 curl 的严格握手校验。

---

## ⚖️ 执行内核与 FinOps 预算管控

系统提供双内核架构，支持根据任务重要级别与预算策略动态切换：

| 内核模式 (RUNNER) | 通信机制 | 预算闸门与消耗控制 | 适用场景 |
|---|---|---|---|
| **`mock`**（默认测试） | 进程内直接调度 | 开跑前整笔预扣（消耗 0） | 单元测试、CI 流水线、逻辑自检 |
| **`mock_acp`** | JSON-RPC (Mock 服务端) | 开跑前预扣 + 越线实时取消（消耗 0） | 协议层兼容性测试 |
| **`dsh`** | 子进程 `dsh --profile headless` | 开跑前整笔预扣（黑盒模型，中途无法中断） | 离线批量无交互任务 |
| **`acp`** ⭐ (生产推荐) | `dsh --profile acp` 双向 JSON-RPC | **每次工具调用后推算用量，一旦越线立即触发 `session/cancel` 熔断** | 企业生产实盘任务、预算敏感场景 |

> **FinOps 双口径核算**：任务消耗（`task_runs.usage_tokens`）与阶段 Overhead（立项、研讨、WBS 拆解等管理调度调用）分列计算，并在阶段达 80% 预算线时前置预警熔断，按实时人民币汇率折算账单。

---

## 🏁 快速开始与访问指南

### 1. 服务端一键拉起 (Linux VPS / 自备服务器)

在您的自备服务器上直接执行单行脚本，自动完成环境安装、自签证书、systemd 守护进程与服务启动：

```bash
curl -sSL https://raw.githubusercontent.com/julingwu/agent-OS/mainline/scripts/setup_server.sh | bash
```

### 2. Windows 本地一键启动

```powershell
# 运行一键全栈启动脚本
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
```

启动完成后，系统提供两个标准访问入口：

* **【核心主工作台】（登录默认主界面）**：**`http://127.0.0.1:8077/`**（或 `http://127.0.0.1:8077/ui/`）
  * 包含 43 岗 AI 助手、多轮协作会话、右侧桌面实时流控制、技能配置。
* **【赛博全息指挥舱】（工程总装第二舱）**：**`http://127.0.0.1:8077/app/`**
  * 包含 9 阶段软件工程流水线、OCTO 研讨沙盘、MCP 交易所，顶部常驻【← 返回主工作台】。

### 3. Windows 原生桌面客户端 (`AgentOS.exe`)

> 💡 **绿色免安装单文件**：仅 6.16 MB，解压或直接双击运行，内置全套 Web 宿主与 Tauri 2 运行时，告别繁琐安装与环境配置。

* 📥 **GitHub 仓库直链下载**：[AgentOS.exe (点击直链下载)](https://github.com/julingwu/agent-OS/raw/mainline/releases/AgentOS.exe)
* 🚀 **云端高速下载镜像**：[https://193.112.172.223/ui/AgentOS.exe](https://193.112.172.223/ui/AgentOS.exe)

**核心特性**：
* 离线内置完整前端包，无需预先启动浏览器；
* 内置主机管理器，支持在官方节点、自备主机、本地开发机之间自由切换并实时探活 Ping 延迟；
* 点击左下角【赛博指挥舱】可随时切入工程视图，点击右上角【← 返回对话】秒级回到工作台。

### 4. Android / 鸿蒙移动端 APK

代码仓库已配置 **GitHub Actions** 自动化移动构建流水线：
* 访问 GitHub 仓库 ➔ **Actions** ➔ **Build Android APK (Tauri 2 Mobile)** ➔ 下载最新的 Artifacts 安装包；
* 手机端自适应主流机型分辨率，提供双向侧滑抽屉与安全区沉浸式体验。

---

## 🛡️ 质量保证与测试套件

我们坚持“测试先于交付”的严谨工程原则。全仓库涵盖 **89 个专业测试文件**，覆盖权限、调度、护栏、事件、进程以及跨平台 API：

```powershell
# 运行全量自动化测试
.\.venv\Scripts\pytest.exe -q
```

**测试执行结果**：
```text
........................................................................ [  8%]
........................................................................ [ 17%]
........................................................................ [ 26%]
........................................................................ [ 35%]
........................................................................ [ 43%]
........................................................................ [ 52%]
........................................................................ [ 61%]
........................................................................ [ 70%]
........................................................................ [ 78%]
........................................................................ [ 87%]
........................................................................ [ 96%]
...............................                                          [100%]
823 passed, 1 warning in 461.40s
```
> **100% 通过（823 passed）**，无一挂起，无一报错。

---

## 📂 仓库工程结构

```text
oaagent-m0/
├── app/                      # 后端核心业务层 (FastAPI + SQLite WAL)
│   ├── agent/                # 循环 Agent 核心与工具调用链
│   ├── api_v1/               # v1 统一对外 API (Bot/会话/设备/流式/角色/技能)
│   ├── computer/             # 桌面操控与交互抽象层
│   ├── local_bridge.py       # 本地硬件与网络网关接入
│   ├── local_policy.py       # 结构化护栏与目录白名单安全策略判定
│   ├── processes.py          # W4 跨平台进程 Supervisor
│   ├── rbac.py               # 租户隔离与角色权限访问控制
│   └── events.py             # 全局流式事件总线 (SSE/WebSocket)
├── releases/                 # 免安装绿色发布包 (AgentOS.exe 桌面客户端)
├── desktop/                  # 核心主工作台前端 (Tauri 2 客户端 + Web 宿主)
│   ├── src/                  # React + Vite 响应式主界面 (图 1: 对话/电脑操控/43角色/技能)
│   └── src-tauri/            # Tauri 2 原生封装 (Windows exe / Android APK)
├── web/                      # 赛博全息指挥舱前端 (图 2: 总装流水线/研讨沙盘/MCP交易所)
├── ui_dist/                  # 服务端托管的主工作台静态构建包 (对应 /ui/)
├── deploy/                   # 部署与运维配置 (Nginx 反代 / TLS 自签 CA)
├── docs/screenshots/v5/      # 系统最新真机截图 (workspace_main.png / cyberdeck.png)
├── scripts/                  # 一键启动脚本 (start_all.ps1 / setup_server.sh)
├── tests/                    # 89 个全面回归测试套件 (823 条用例)
└── .github/workflows/        # GitHub Actions 自动化打包流水线 (Android APK)
```

---

<div align="center">
  <sub>AgentOS · 次世代企业级自主智能体协作操作系统 · 双舱协同 · 保障数据主权与物理安全底线</sub>
</div>
