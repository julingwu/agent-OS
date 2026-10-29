<div align="center">

# 🤖 AgentOS (OA Agent) · 次世代企业级自主智能体协作操作系统

**多端全双工协同 · 55+ 类行业岗位角色 · 动态技能组合 · 桌面原生操控流 · 金融级安全信道 · 自备主机私有化**

<p align="center">
  <img src="https://img.shields.io/badge/Version-v5.5.0%20%7C%203D%20HQ%20Edition-blue.svg?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/Automated%20Tests-823%20Passed%20(100%25)-success.svg?style=flat-square" alt="Tests">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Android%20%7C%20Web-orange.svg?style=flat-square" alt="Platform">
  <img src="https://img.shields.io/badge/Roles%20Library-55%2B%20Personas%20%C3%97%2010%20Domains-purple.svg?style=flat-square" alt="Roles">
  <img src="https://img.shields.io/badge/3D%20Engine-WebGL%20HQ%20Intelligent%20Sandpit-cyan.svg?style=flat-square" alt="3D HQ">
  <img src="https://img.shields.io/badge/Security-TLS%201.3%20%2B%20CDP%2FCRL%20%2B%20Hard%20Guardrails-red.svg?style=flat-square" alt="Security">
</p>

<p align="center">
  一句话下达业务需求 ➔ 55+ 岗 AI 助手协同 ➔ 实时接管操作电脑 ➔ 3D 智能总部全景沙盘 ➔ L0/L1/L2 严苛验收 ➔ 组织经验自动沉淀
</p>

<p align="center">
  <a href="https://github.com/julingwu/agent-OS/releases/download/v5.5.0/AgentOS.exe">
    <img src="https://img.shields.io/badge/Windows%20客户端-AgentOS.exe%20(6.16MB)-6366f1?style=for-the-badge&logo=windows&logoColor=white" alt="Download Windows Client" />
  </a>
  <a href="https://193.112.172.223/ui/AgentOS.exe">
    <img src="https://img.shields.io/badge/云端极速镜像-AgentOS.exe-10b981?style=for-the-badge&logo=cloud&logoColor=white" alt="Cloud Download Mirror" />
  </a>
  <a href="https://github.com/julingwu/agent-OS/releases/download/v5.5.0/AgentOS.apk">
    <img src="https://img.shields.io/badge/Android%20客户端-AgentOS.apk%20(118MB)-3DDC84?style=for-the-badge&logo=android&logoColor=white" alt="Download Android Client" />
  </a>
  <a href="https://193.112.172.223/ui/AgentOS.apk">
    <img src="https://img.shields.io/badge/云端极速镜像-AgentOS.apk-059669?style=for-the-badge&logo=cloud&logoColor=white" alt="Cloud Android Mirror" />
  </a>
</p>

---

</div>

## 📑 目录导航

- [✨ 双舱协同架构：主工作台与赛博指挥舱](#-双舱协同架构主工作台与赛博指挥舱)
- [🖼️ 界面全景与真实运行效果](#️-界面全景与真实运行效果)
  - [1. 核心主工作台（登录后默认主界面）](#1-核心主工作台登录后默认主界面)
  - [2. 赛博指挥舱（3D HQ 智能总部沙盘 + 13 大企业级管理视图）](#2-赛博指挥舱3d-hq-智能总部沙盘--13-大企业级管理视图)
  - [3. 55+ 类细分行业角色库 × 10 大专业领域全覆盖](#3-55-类细分行业角色库--10-大专业领域全覆盖)
- [🏛️ 系统架构全景拓扑](#️-系统架构全景拓扑)
- [🚀 六大核心技术支柱](#-六大核心技术支柱)
- [⚖️ 执行内核与 FinOps 预算管控](#️-执行内核与-finops-预算管控)
- [🏁 快速开始与客户端下载](#-快速开始与客户端下载)
- [🛡️ 质量保证与测试套件](#️-质量保证与测试套件)
- [📂 仓库工程结构](#-仓库工程结构)
- [🙏 开源致谢与说明](#-开源致谢与说明)

---

## ✨ 双舱协同架构：主工作台与赛博指挥舱

传统的 AI 框架要么是“单调的聊天框”，要么是“复杂的后台流水线看板”，割裂了人机对话协作与宏观工程交付。

**AgentOS** 采用独特的**双舱协同架构**，各司其职又无缝秒级跳转：

```mermaid
flowchart LR
    subgraph CoreWorkspace ["【第一主舱】AI 团队协同工作台 (默认主界面)"]
        Chat["💬 多助手多轮协作会话<br/>(小万、小财等 55+ 行业岗位)"]
        Stream["🖥️ 实时桌面推流与键鼠接管<br/>(低延迟推流 + Win32 原生交互)"]
        CloudHost["☁️ 云端全托管持久执行<br/>(客户端关闭/手机锁屏断网服务端照常跑)"]
        Scroll["⚓ 深色极简滚动条与一键触底<br/>(↓ 悬浮锚点 + reasoning_content 全兼容)"]
        RolesConfig["🎭 岗位角色与技能自由定制<br/>(自带基础技能 + 动态扩展)"]
    end

    subgraph CyberDeck ["【第二切换舱】赛博指挥舱 (3D HQ 智能总部 + 13 大视图)"]
        HQ["🏢 3D HQ 智能总部沙盘<br/>(回字形单层 / 5档昼夜光影 / 实时阴影)"]
        NekoChief["🐱 总指挥「小枢」统筹调度<br/>(8大职能部门 / 31名Q版寻路 / 周会评审)"]
        LiveLink["⚡ 真实业务直连<br/>(云端立项 / /ws/metacorp / 80%预算熔断)"]
        MCPEx["🔌 万界 MCP 交易所<br/>(工具资产交易 / 蓝图装配)"]
        Views2D["📊 13 大企业级管理视图<br/>(工作台/总装流水线/任务/会话/产物/审批/用量...)"]
    end

    CoreWorkspace -- "左侧导航 / 顶栏一键切换「赛博指挥舱」" --> CyberDeck
    CyberDeck -- "顶部常驻「← 返回 AI 团队主界面」" --> CoreWorkspace
```

1. **第一主舱（AI 团队协同工作台 · 图 1）**：
   * **业务日常协作核心**：用户登录系统后进入的默认核心界面。
   * **55+ 岗 AI 助手团队**：左侧展示团队所有助手（如小说主笔、量化研究员、架构师、法务顾问），支持一对一专聊与多 Agent 群聊协同。
   * **右侧实时电脑视窗与控制（Computer-Use）**：原生推流电脑屏幕，随时查看 Agent 正在电脑上执行的操作，支持一键“取得控制权”直接操作。
   * **云端全托管持久执行**：核心任务全生命周期托管于云端服务，**关闭客户端 EXE 或手机锁屏断网后，服务端任务照常运行到底或遇阻等待反馈**，重新打开客户端无缝恢复最新进度。
   * **极致人机工程学体验**：重构深色极简半透明滚动条，新增一键回到底部 `↓` 悬浮锚点，深度兼容深度思考模式（`reasoning_content`）完整思维链展示。
   * **角色与技能自由定制**：新建助手时从 55+ 个专业角色预设挑选（覆盖文学创作、量化科技、自媒体、个人幕僚等 10 大分类），自动初始化核心技能。

2. **第二切换舱（赛博指挥舱 · 3D HQ 智能总部 + 13 大企业级管理视图 · 图 2）**：
   * **原生 WebGL 3D HQ 智能总部沙盘**：基于轻量原生 WebGL 渲染管线打造的「回字形」单层 3D 企业智能总部沙盘，具备实时阴影、5 档昼夜光影切换、移轴景深与泛光特效；正中总指挥 **「小枢」** 统筹 **产品部、设计部、市场部、运营部、研发部、人事部、财务部、法务行政部** 8 大职能部门，31 名 Q 版角色在走廊依照最短路径动态寻路、跨部门递送文件夹、参与周会评审与茶水间小憩。
   * **实盘业务数据无缝联动**：与 AgentOS 云端后端深度打通，支持一键立项并派发任务、实时监听 `/ws/metacorp` 事件总线、统计各部门实时 Token 消耗账本与 80% 阶段预算熔断预警，内置 **万界 MCP 交易所** 支持一键安装扩展工具蓝图。
   * **完整 13 大专业 2D 管理视图**：左侧完整保留 **工作台、总装流水线、任务中心、协作会话、工作产物、审批中心、组织与团队、角色库、用量与费用、评测中心、组织经验、工具与集成、系统维护** 13 大企业级管理视图，支持随时展开任务审查与审批流。
   * **双向无缝切回**：指挥舱顶部常驻【← 返回 AI 团队主界面】按钮，随时一键回归对话工作台。

---

## 🖼️ 界面全景与真实运行效果

### 1. 核心主工作台（登录后默认主界面）
> 55+ 岗 AI 助手团队、多轮上下文会话、右侧原生电脑操控流推流与接管、岗位技能自定义增删、云端持久执行与一键触底锚点。

<div align="center">
  <img src="docs/screenshots/v5/workspace_main.png" width="920" alt="核心主工作台（登录后主界面）">
</div>

### 2. 赛博指挥舱（3D HQ 智能总部沙盘 + 13 大企业级管理视图）
> 原生 WebGL「回字形」3D 智能总部沙盘、总指挥「小枢」调度 8 大部门与 31 名员工、动态光影与事件流、实盘任务创建、MCP 交易所与 13 大企业级管理视图。随时点击顶部 `← 返回 AI 团队主界面` 秒级切回。

<div align="center">
  <img src="docs/screenshots/v5/cyberdeck.png" width="920" alt="赛博指挥舱 3D HQ 智能总部">
</div>

---

### 3. 55+ 类细分行业角色库 × 10 大专业领域全覆盖

系统数据库内建 **55+ 类标准化企业与个人超级角色**（涵盖小说网文、自媒体 AIGC、硬科技量化、独立智库等 10 大矩阵），并出厂预植工业级执行流程技能（新建角色自动绑定专属技能集，且可在会话中随时借调扩展）：

| 行业领域 (10 大分类) | 代表性预设角色 (共 55+ 类) | 专属执行技能与交付物示例 |
|---|---|---|
| **文学与创作** ⭐ | **网文大神 / 小说主笔**、**故事架构师 / 文学责编**、**影视 / 短剧编剧**、**世界观与考据设定师** | **世界观与设定集构建**、**黄金三章与大纲设计**、**密集反转与钩子设计**、**力量体系与考据百科** |
| **自媒体与创作** ⭐ | **自媒体爆款操盘手**、**AIGC视觉与分镜导演** | **全平台爆款标题与封面文案**、**小红书种草图文**、**AI绘画/视频分镜提示词工程**、**角色一致性方案** |
| **金融与硬科技** ⭐ | **MQL5 / 量化交易工程师**、**硬科技产业链首席分析师**、**量化研究员**、**投研分析师** | **MQL5/EA 自动交易策略**、**动态仓位与回撤风控**、**硬科技产业链穿透拆解**、**单品盈利模型与估值对比** |
| **个人智库与商业** ⭐ | **全能私人幕僚长 (Chief of Staff)**、**Prompt与Agent架构师**、**学术文献与深度研究官**、**独立开发者与商业顾问** | **本地文件与桌面自动化整理**、**结构化 System Prompt 设计**、**多 Agent 工作流编排**、**前沿论文拆解**、**MVP商业变现** |
| **技术研发** | 后端架构师、前端工程师、测试开发、DevOps 工程师、数据平台工程师、本地化翻译 | **代码质量与安全审计**、**高并发微服务设计**、**React 组件化**、**CI/CD 自动化流水线** |
| **法务与合规** | 企业合同审查律师、知识产权顾问、数据合规顾问、劳动争议顾问 | **合同审查与风险条款识别**、**出海知识产权布局**、**数据合规评估**、**劳动关系合规指引** |
| **运营与增长** | 用户运营、内容运营、活动运营、私域运营、业务数据分析师 | **用户分层与促活召回**、**社群企微触达 SOP**、**大型促销活动操盘**、**指标异动归因分析** |
| **市场与品牌** | 品牌策划、增长运营、投放优化师、市场调研员 | **品牌定位与心智提炼**、**整合传播节奏**、**信息流投放与出价策略**、**竞品对标与用户访谈** |
| **设计与视觉** | UI视觉设计师、交互设计师、品牌视觉师、动效设计师 | **移动端界面与设计规范**、**可点击流程原型**、**VI 视觉延展**、**微交互转场动效** |
| **通用与管理** | Python极客、严苛的测试员、产品经理 (CPO)、系统架构师、合规审计官、SRE 专家 | **第一性原理决策推演**、**最小可运行原型 (MVP)**、**红线合规一票否决**、**发布回滚监控清单** |

---

## 🏛️ 系统架构全景拓扑

```mermaid
flowchart TB
    subgraph ClientLayer ["客户端层 (Client Layer - Tauri 2 Multi-Target)"]
        WinClient["🖥️ Windows 桌面端 (AgentOS.exe)<br/>(Tauri 2 原生封装 / GDI 抓屏 / 独立进程组)"]
        WebClient["🌐 现代浏览器 Web 端 (/app/ & /ui/)<br/>(3D HQ WebGL 沙盘 + 13 大企业视图 + 双舱秒级跳转)"]
        MobileClient["📱 Android / 鸿蒙移动端 (AgentOS.apk)<br/>(原生 APK / 安全区与双向侧滑抽屉自适应)"]
    end

    subgraph SecurityLayer ["安全与合规网关 (Enterprise Security Gateway)"]
        TLS["🔒 双级自建 CA 证书链<br/>(RSA 4096 根 + 397天叶证书 + Nginx 443 DER CRL 吊销点)"]
        Guardrails["🛡️ 结构化护栏引擎<br/>(高危操作审批卡 + local_allow_dirs 路径白名单硬阻断)"]
        AuthRBAC["🔑 租户隔离与 RBAC<br/>(租户物理隔离 + CEO/主管/执行员多角色鉴权)"]
    end

    subgraph CoreEngine ["核心调度与执行引擎 (Core Agent Engine)"]
        FastAPI["⚡ 高性能调度中枢 (FastAPI)"]
        DB[(🗄️ SQLite WAL 引擎<br/>bot_skills / 55+角色库 / 租户数据物理隔离)]
        Supervisor["⏱️ W4 进程 Supervisor<br/>(独立日志落盘 / 跨重启 recover_interrupted 自动愈合)"]
        DualKernel["⚙️ 执行内核切换器<br/>(NativeKernel 进程内直驱 vs ACP 子进程 JSON-RPC)"]
        CloudPersist["☁️ 全托管执行守护器<br/>(客户端断连/断网无感，服务端持久化运行并按序回调)"]
    end

    subgraph CapabilityLayer ["智能体角色与能力资产 (Personas & Capabilities)"]
        Roles["🎭 55+ 类行业岗位角色库<br/>(文学/自媒体/智库/金融/研发/法务/运营 全矩阵)"]
        Skills["🧰 12 项工业级特化技能库<br/>(决策推演 / 代码审计 / 财税合规 / 周报复盘)"]
        MCPBridge["🔌 MCP 协议桥接<br/>(标准外部工具接入 / 多生态模型扩展)"]
    end

    WinClient --> TLS
    MobileClient --> TLS
    WebClient --> TLS
    TLS --> Guardrails --> AuthRBAC --> FastAPI

    FastAPI --> DB
    FastAPI --> Supervisor
    FastAPI --> CloudPersist
    FastAPI --> DualKernel

    DualKernel --> Roles
    DualKernel --> Skills
    DualKernel --> MCPBridge
```

---

## 🚀 六大核心技术支柱

### 1. 核心工作台即主入口，多 Agent 智能协同
* **拒绝千篇一律的通用对话**：系统内置 55+ 个细分行业角色标准定义，登录即直达多 Agent 协作主界面。
* **岗位创建即赋能**：新建助手时挑选角色，系统自动为其初始化核心 Prompt、能力标签与专属基础技能。
* **自由扩展与跨角色赋能**：助手详情面板支持直观增删技能，提供本角色推荐技能库，并支持从其它角色中按需借调。

### 2. 桌面原生执行桥（Desktop Native Bridge）与实时视窗
* **实时桌面推流**：主工作台右侧直接挂载桌面画面，随时观察 Agent 执行操作；
* **Win32 深度硬件交互**：通过 Windows API 实现前台活动窗口枚举与聚焦、多屏坐标计算；
* **超轻量原生抓屏**：利用 Win32 GDI 配合纯 Rust `png` 无损压缩算法抓屏，381ms 极速生成，规避重量级依赖。

### 3. 云端全托管持久执行与自备主机（BYOH）
* **断网/关机无感执行**：执行任务全生命周期托管于服务器，即使客户端关闭或手机锁屏，服务端仍持续执行任务，杜绝掉线丢失进度。
* **数据主权私有化**：服务端主机与平台账号物理解耦。企业与个人用户自备 Linux VPS 或本地服务器，数据主权完全私有。
* **一键单行脚本拉起**：在服务器终端执行一行 curl 命令即可完成环境准备与服务守护进程注册。
* **客户端多实例管理**：客户端内置动态服务器切换器与 Ping 延迟探活，一键在“官方节点”、“自备云机”与“局域网实例”间切换。

### 4. 企业级严格护栏与人机共治（Guardrails & HITL）
* **三级权限审批与会话级静默免打扰**：
  * **单次授权 (`once`)**：高危操作（如系统脚本执行、敏感目录文件操作）逐次弹出结构化审批卡由人工批准；
  * **本次会话免打扰 (`session`)**：支持一键开启“本次会话免打扰”，同会话后续同类型安全动作自动静默放行，杜绝高频弹窗干扰；
  * **即时拒绝 (`reject`)**：对不可接受的操作一键拒绝阻断，守护本地安全底线。
* **三层可信验收标准**：
  * **L0 规则自动判定**：语法校验、状态码检查、格式匹配；
  * **L1 人工签批强制留痕**：审批人必须填写 `evidence_snippet` 凭据，**严禁大模型自评通过**；
  * **L2 沙箱脚本真实执行**：在隔离沙箱中跑自动化判据脚本，真实退出码作为唯一通过证据。
* **结构化高危审批卡**：直观展示操作对象人话摘要、目标路径、增减字符量与变更原文预览，消灭“盲批”隐患。
* **目录白名单硬阻断**：服务端全局配置 `local_allow_dirs`，越界路径静默拦截拒绝，不给任何越权穿透机会。

### 5. 全双工跨端协同（Windows Desktop + Web + Android Mobile）
* **Tauri 2 统一架构**：桌面与移动端共享一套高质量 React 前端，桌面端编译为轻量 Win32 原生可执行文件（6.16 MB），移动端输出原生 Android APK（118 MB）。
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

## 🏁 快速开始与客户端下载

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

启动完成后，系统提供两个标准 Web 访问入口：

* **【核心主工作台】（登录默认主界面）**：**`http://127.0.0.1:8077/`**（或 `http://127.0.0.1:8077/ui/`）
  * 包含 55+ 岗 AI 助手、多轮协作会话、右侧桌面实时流控制、技能配置。
* **【赛博指挥舱】（工程总装第二舱）**：**`http://127.0.0.1:8077/app/`**
  * 包含 3D HQ 智能总部沙盘、总指挥「小枢」、8 大职能部门、MCP 交易所与 13 大企业管理视图，顶部常驻【← 返回主工作台】。

### 3. Windows 原生桌面客户端 (`AgentOS.exe`)

> 💡 **绿色免安装单文件**：仅 6.16 MB，解压或直接双击运行，内置全套 Web 宿主与 Tauri 2 运行时，告别繁琐安装与环境配置。

* 📥 **GitHub Releases 官方下载**：[AgentOS.exe (v5.5.0 点击下载)](https://github.com/julingwu/agent-OS/releases/download/v5.5.0/AgentOS.exe)
* 📥 **GitHub 仓库直链备用**：[AgentOS.exe (raw 链接)](https://github.com/julingwu/agent-OS/raw/mainline/releases/AgentOS.exe)
* 🚀 **云端极速下载镜像**：[https://193.112.172.223/ui/AgentOS.exe](https://193.112.172.223/ui/AgentOS.exe)

### 4. Android / 鸿蒙移动端客户端 (`AgentOS.apk`)

> 📱 **移动协同随时随地**：针对触控屏优化双向抽屉交互，完整支持实时会话、审批流签批与云端任务巡检。

* 📥 **GitHub Releases 官方下载**：[AgentOS.apk (v5.5.0 点击下载 · 118MB)](https://github.com/julingwu/agent-OS/releases/download/v5.5.0/AgentOS.apk)
* 🚀 **云端极速下载镜像**：[https://193.112.172.223/ui/AgentOS.apk](https://193.112.172.223/ui/AgentOS.apk)

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
│   ├── src/                  # React + Vite 响应式主界面 (图 1: 对话/电脑操控/55+角色/技能)
│   └── src-tauri/            # Tauri 2 原生封装 (Windows exe / Android APK)
├── web/                      # 赛博指挥舱前端 (图 2: 3D HQ 智能总部沙盘 + 13 大企业视图)
│   ├── src/
│   │   ├── CyberHQEngine.jsx # 3D HQ WebGL 沙盘 React 容器封装
│   │   ├── hq_engine_core.js # 3D HQ 智能总部原生 WebGL 核心渲染引擎
│   │   └── CyberFactorioDeck.jsx # 赛博指挥舱主控板与万界 MCP 交易所
├── ui_dist/                  # 服务端托管的主工作台静态构建包 (对应 /ui/)
├── deploy/                   # 部署与运维配置 (Nginx 反代 / TLS 自签 CA)
├── docs/screenshots/v5/      # 系统最新真机截图 (workspace_main.png / cyberdeck.png)
├── scripts/                  # 一键启动脚本 (start_all.ps1 / setup_server.sh)
├── tests/                    # 89 个全面回归测试套件 (823 条用例)
└── .github/workflows/        # GitHub Actions 自动化打包流水线 (Android APK)
```

---

## 🙏 开源致谢与说明

- **「10 · HQ 智能总部」3D 引擎沙盘**：基于 [@赛博老爸](https://github.com/cyberdaddyleo) 的开源项目 [`cyberdaddyleo/agent-display`](https://github.com/cyberdaddyleo/agent-display) v10.0 HQ 智能总部深度集成改造，遵循 [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) 许可规范。在此向作者的卓越视觉工程与沙盘构想致以诚挚感谢！
- **Tauri 2**：提供极速轻量的跨端原生打包支持。
- **FastAPI**：提供高性能异步调度 API 与 WebSocket 实时流。

---

<div align="center">
  <sub>AgentOS · 次世代企业级自主智能体协作操作系统 · 双舱协同 · 保障数据主权与物理安全底线</sub>
</div>
