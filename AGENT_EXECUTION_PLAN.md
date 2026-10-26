# AGENT_EXECUTION_PLAN: Agent OS (oaagent-m0) v5.1 -> v5.2 止血与提率工程规约

```yaml
meta:
  project: oaagent-m0 (Agent OS)
  baseline_commit: "924346b"
  target_version: "5.2.0"
  execution_mode: sequential_sprints
  hard_invariants:
    - "严禁关闭、删除或绕过 SQLite 的 5 个触发器 (trg_stage_no_skip, trg_stage_gate_guard, trg_gate_veto_ins, trg_gate_veto_upd, trg_tasks_state_guard)"
    - "严禁将 uvicorn 改为多 worker；保持 SQLite WAL 单写架构"
    - "严禁让模型自评通过 (L1 判据必须校验 evidence_snippet，L2 必须真实执行沙箱脚本)"
    - "严禁修改 web/vite.config.js 中的 base: './' (保护 Tauri 桌面端协议兼容性)"
    - "严禁引入 React Router 或外部重型状态库 (Redux/Zustand)"
    - "严禁给工作区根目录打 Windows Low 完整性标签 (仅对单任务子目录打 Low 标签，确保 test_m0_20 通过)"
```

---

## SPRINT 1: 物理地基止血与测试全绿 (P0 / 阻塞级修复)

> **目标**：消除新库立项崩溃、消除双内核路由吞语义、消除测试污染生产库、统一产物目录寻址，使全量 pytest 100% 变绿。

### TASK-1.1: 修复 `project_stages` 建表 DDL 缺列与启动期自动迁移 (修复缺陷 #1)

* **Target Files**: `app/pipeline.py`, `app/flows.py`
* **Context**: `app/pipeline.py:51` 的 `CREATE TABLE IF NOT EXISTS project_stages` 缺少 `role_tag`、`node_kind`、`gate_name` 三列，而 `app/pipeline.py:259` 在立项写入时强依赖这三列，导致干净数据库首次立项必崩（31 项测试失败）。
* **Action Steps**:
1. 打开 `app/pipeline.py`（约第 51 行），在 `project_stages` 的建表 DDL 中补齐三列：
* `role_tag TEXT DEFAULT 'dev'`
* `node_kind TEXT DEFAULT 'standard'`
* `gate_name TEXT DEFAULT ''`
2. 将 `app/flows.py:173` 中的 `ALTER TABLE project_stages ADD COLUMN ...` 幂等迁移逻辑抽离或确保在数据库连接初始化（`init_db`）时无条件优先执行，保证存量旧库与新空库结构完全一致。
* **Verification Cmd**:
```powershell
pytest tests/ -k "stage or pipeline or flow" --tb=short
```
* **DoD (Definition of Done)**: 在全新临时 SQLite 库上调用 `pipeline.create_project` 不抛 `OperationalError: table project_stages has no column named ...`，关联的 31 个失败测试全部转绿。

### TASK-1.2: 修复双内核路由吞语义与 Native 工具调用上限 (修复缺陷 #2)

* **Target Files**: `app/engine/dsh_adapter.py`, `app/engine/kernel_router.py`, `app/engine/runner_native.py`, `app/engine/runner_acp.py`
* **Context**: `app/engine/dsh_adapter.py:633` 将 `acp` / `mock_acp` / `native` / `auto` 全部交给 `kernel_router`，而 `kernel_router` 默认 `auto_failover` 且 `M0_PREFER_DSH=0`，导致显式指定 `acp` 或 `mock_acp` 时全部跑进了 `NativeKernel`，7 个守卫测试因此变红。
* **Action Steps**:
1. 修改 `app/engine/dsh_adapter.py`（约第 633 行）与 `app/engine/kernel_router.py`：
* 当传入 `runner == 'mock_acp'` 时，强制走 Mock ACP 流程，严禁路由至 Native。
* 当传入 `runner == 'acp'` 或 `runner == 'dsh'` 时，强制拉起 ACP/DSH 通道。
* 仅当 `runner == 'auto'` 或 `runner == 'auto_failover'` 时，才执行智能路由：若任务绑定的工具集（查 `app/tools_acl.py`）包含高危工具（`db_exec`, `file_delete`, `shell_exec`）或设置了 `M0_PREFER_DSH=1`，路由至 ACP；否则路由至 `NativeKernel`。
2. 将 `app/engine/runner_acp.py` 中的 `_tool_cap_check`（搜索 > 5 次 / 抓取 > 3 次直接 cancel 并记 `tool_cap_exceeded`）逻辑同步移植到 `app/engine/runner_native.py` 的工具执行循环中。
* **Verification Cmd**:
```powershell
pytest tests/ -k "acp or kernel or runner or cap" --tb=short
```
* **DoD**: 7 个断言 `-9` 退出码及 `runner == 'mock_acp'` 的守卫测试全部 passed。

### TASK-1.3: 工作区目录寻址单一化与 Pytest 收集隔离 (修复缺陷 #4, #7)

* **Target Files**: `app/engine/runner_native.py`, `app/pipeline.py`, `pytest.ini` (或 `pyproject.toml`)
* **Context**: `app/engine/runner_native.py:49` 将产物写到仓库根 `workspace/`，而 `app/pipeline.py:_wdir` 写到 `%LOCALAPPDATA%\oaagent-m0\workspace`，导致 L2 验收脚本和 `release` 打包读不到最新产物；同时仓库根 `workspace/projects/*/test_check.py` 导致根目录跑 `pytest` 收集阶段直接中断。
* **Action Steps**:
1. 创建统一的公共路径解析函数（如在 `app/config.py` 或 `app/pipeline.py` 中定义 `get_workspace_root()`）：优先读取环境变量 `M0_WORKSPACE`；若未设置，统一返回 `%LOCALAPPDATA%\oaagent-m0\workspace`。
2. 修改 `app/engine/runner_native.py:49` 与 `app/pipeline.py:_wdir`（以及 `app/acceptance_run.py` 中的产物读取路径），全部调用 `get_workspace_root()`，彻底消灭硬编码的 `./workspace`。
3. 在仓库根目录创建或更新 `pytest.ini`，写入以下配置：
```ini
[pytest]
testpaths = tests
norecursedirs = workspace vendor dist build node_modules .git
```
* **Verification Cmd**:
```powershell
pytest --collect-only
```
* **DoD**: 在仓库根目录直接运行 `pytest` 零收集错误；`NativeKernel` 生成的文件与 `pipeline.run_stage('release')` 打包的目录 100% 指向同一物理路径。

### TASK-1.4: 测试数据库强制隔离与仓库卫生清理 (修复缺陷 #3, #5, #6, #10, #11)

* **Target Files**: `tests/conftest.py`, `tests/test_v51_*.py`, `e2e_supervisor.py`, `app/main.py`, `app/ui/ops.py`, `src-tauri/tauri.conf.json`
* **Action Steps**:
1. 在 `tests/conftest.py` 中添加 `autouse=True` 的 session/function 级 fixture：强制将环境变量 `DB_PATH` 指向 `tmp_path / "test_oa.db"`，将 `M0_WORKSPACE` 指向 `tmp_path / "workspace"`，将 `WECOM_MOCK=1` 设为默认（防止测试或启动恢复 `recover_interrupted` 时真打企微 API 报 60020 错误）。
2. 检查所有 `tests/test_v51_*.py` 文件，移除任何硬编码或覆盖生产库 `./data/oa.db` 的逻辑，并添加一句守卫断言：`assert "data/oa.db" not in os.path.abspath(os.getenv("DB_PATH", "")).replace("\\", "/")`。
3. 修改 `e2e_supervisor.py:25-26`：移除硬编码的 CEO 身份 ID 与 64 位明文 secret，改为 `os.environ["CEO_IDENTITY_ID"]` 与 `os.environ["CEO_SECRET"]`。
4. 删除 0 字节的废弃空库文件：`data/agent_os.db`、`data/oaagent.db`；在 `web/public/` 下补一个最小合法的 `favicon.ico` 消除控制台 404。
5. 统一版本号：将 `app/main.py:88`、`app/ui/ops.py:21`、`src-tauri/tauri.conf.json` 中的版本号从 `4.3.0` 统一更新为 `5.1.0`。
* **Verification Cmd**:
```powershell
pytest
```
* **DoD**: 全量测试 0 failed, 0 errors；测试跑完后检查 `data/oa.db` 的 `mtime` 未发生变化。

---

## SPRINT 2: 交付完成率拉升工程 (目标：完成率从 2.4% 提升至 >35%)

> **目标**：解决“所有需求强制陪跑 9 阶段”与“一套死板判据打所有题材”导致的 77% 驳回率问题。

### TASK-2.1: 按流程模板动态裁剪立项阶段 (修复缺陷 #8 & 解决 §25 问题 5)

* **Target Files**: `app/flows.py`, `app/pipeline.py`, `tests/test_v41_flows.py`, `web/src/CyberFactorioDeck.jsx`
* **Context**: `app/flows.py` 已定义 4 阶段的 `FLOW-ANALYSIS`、`FLOW-RESEARCH`、`FLOW-URGENT`，但 `app/pipeline.py:229` (`create_project`) 仍硬编码一次性写入 9 个阶段；且 `FLOW-STANDARD` 与 `FLOW-SOFTWARE` 完全重复。
* **Action Steps**:
1. 在 `app/flows.py` 中移除冗余的 `FLOW-STANDARD`（或将其作为 `FLOW-SOFTWARE` 的别名兼容映射），更新 `tests/test_v41_flows.py:69` 的阶段数量断言为 9。
2. 重构 `app/pipeline.py:229` 的 `create_project`：接收 `flows.plan` 产出的阶段节点列表（4 阶段模板就只向 `project_stages` 和 `stage_gates` 插入对应的 4 行记录，严格保持 `seq` 连续递增，从而无缝兼容 `trg_stage_no_skip` 触发器）。
3. 修改前端 `web/src/CyberFactorioDeck.jsx`：站点渲染列表不再写死 9 个固定站点，而是根据当前选中项目实际返回的 `stages` 数组动态点亮（若无选中项目则默认展示标准 9 站点）。
* **Verification Cmd**:
```powershell
pytest tests/test_v41_flows.py tests/ -k "project or stage"
```
* **DoD**: 下发一条调研类需求（命中 `FLOW-RESEARCH`），数据库 `project_stages` 仅生成 4 个阶段，且走完第 4 阶段后项目正常流转至 `done`。

### TASK-2.2: 按 Pack 与阶段类型对齐 L2 确定性判据 (落地 §24-B1)

* **Target Files**: `app/acceptance_run.py`, `app/acceptance/l2_scripts.py`, `app/engine/task_generator.py`
* **Context**: 597 个任务中 459 个 `rejected`，主因是文稿、分析、调研类任务被套用了软件工程类的 L2 脚本断言，或产出文件名与断言脚本硬编码文件名不一致。
* **Action Steps**:
1. 在 `app/engine/task_generator.py` 生成任务与 `acceptance_criteria` 时，按任务所属的 `pack`（或 `role_tag`：`dev` / `content` / `qa` / `analyst` / `researcher`）绑定差异化的 L2 校验器模板：
* **`dev` / 代码类**：执行语法检查（`py_compile` / `node --check`）+ 沙箱限时运行（exit 0）。
* **`content` / `researcher` / 文稿调研类**：校验目标 Markdown/HTML 文件存在且非空、包含立项书要求的必选二级标题（`##`）、字数落在合理区间（非阻断提示），不跑代码编译断言。
* **`analyst` / 数据分析类**：校验结论报告存在 + 数据产物（JSON/CSV/图表）可合法解析且记录数 > 0。
2. 在 `app/acceptance_run.py:270` 中，当 L2 失败触发重跑时，将具体的失败断言项、期望文件路径及 stderr 摘要结构化注入下一轮 Prompt 的顶部，确保模型单点修正。
* **Verification Cmd**:
```powershell
pytest tests/ -k "acceptance or criteria or l2"
```
* **DoD**: 使用离线评测集 `eval_sets` 运行涵盖软件、分析、调研的基准任务，Task 完成率（`completed`）达到 35% 以上。

---

## SPRINT 3: 闭环自愈、经验降噪与巨石文件拆分

> **目标**：救活库内卡死的 `blocked` 项目，清空 452 条积压的 `pending` 经验，并将前后端超过 2800 行的单文件拆解到可维护规模。

### TASK-3.1: 经验沉淀降噪与批量聚类审核 (落地 §24-B2 & 解决 §25 问题 2)

* **Target Files**: `app/experience.py`, `app/ui/api.py` (governance 路由), `web/src/GovernanceView.jsx`
* **Context**: 当前每次任务 `rejected` 都盲目生成一条 `pending` 经验，导致 452 条低质经验积压在队列中无人审核。
* **Action Steps**:
1. 修改 `app/experience.py` 的自动沉淀触发条件：取消“单次 `rejected` 立即写经验”；改为仅在以下三种情况写入 `org_experiences (state='pending')`：
* 任务经历 `rejected` 后经重跑变为 `completed`（提取失败原因与修复方案的对比差分）；
* 研讨会触发 `veto`（一票否决）；
* 项目完成 `retro`（复盘阶段）。
2. 为 `org_experiences` 与 `eval_sets` 表补充 `tenant_id TEXT DEFAULT 'TENANT-HQ'` 列及查询过滤（完成 §24-D1 租户隔离收口）。
3. 新增后端批量审核接口 `POST /ui/experiences/batch-review`（接收 `exp_ids: list[str]`, `action: 'approve' | 'reject'`），在单事务内更新状态并逐条写入 `ai_audit_logs`（`action='experience_reviewed'`）。
4. 在 `web/src/GovernanceView.jsx` 中增加按标签分组展示与“全选本页 / 批量通过 / 批量驳回”按钮，并提供一键清理无效历史 pending 积压的维护入口。
* **Verification Cmd**:
```powershell
pytest tests/ -k "experience or governance or tenant"
```
* **DoD**: 经验批量审核接口通过测试且审计日志完整；租户 A 无法读取租户 B 的经验与评测集。

### TASK-3.2: 阶段内单任务重跑与 Blocked 项目一键自愈 (落地 §24-B3, B4)

* **Target Files**: `app/pipeline.py`, `app/finance.py`, `web/src/ProjectFlow.jsx`
* **Context**: 当前 `POST /ui/projects/{id}/resume` 在预算划拨后会重跑整个阶段（导致已成功的任务重复烧 Token）；且 3 个 `blocked` 项目缺少界面一键恢复动作。
* **Action Steps**:
1. 修改 `app/pipeline.py` 中的阶段恢复与重跑逻辑：当阶段从 `budget_pause` 或任务失败中恢复时，跳过该阶段内状态已为 `completed` 的 WBS 任务，**仅调度未完成（`pending` / `rejected` / `budget_pause`）的任务**，直接复用已完成任务在 `project_artifacts` 黑板上的产物。
2. 在 `web/src/ProjectFlow.jsx` 的 `FailureDiagnosis` 卡片中，根据阻塞原因渲染一键自愈按钮：
* 若为 `budget_pause`：显示「一键批准追加 30% 阶段额度并续跑未完成任务」；
* 若为 `awaiting_approval`（高危工具拦截）：显示「一键批准平台代执行并 `session/resume`」；
* 若为闸门 `veto`：显示「填写修订意见并重开研讨/解除否决」。
* **Verification Cmd**:
```powershell
pytest tests/ -k "finance or budget or resume or approval"
```
* **DoD**: 模拟阶段内第 1 个任务成功、第 2 个任务触发 80% 预算暂停，划拨恢复后第 1 个任务不再重复执行。

### TASK-3.3: 前端 `App.jsx` 与后端 `app/ui/api.py` 机械式解耦 (修复缺陷 #12)

* **Target Files**: `web/src/App.jsx`, `web/src/views/*`, `app/ui/api.py`, `app/ui/routers/*`
* **Constraint**: 纯物理文件拆分，严禁改变任何 DOM 结构、CSS 类名、接口 URL Path 与鉴权逻辑。
* **Action Steps**:
1. 在 `web/src/context/` 下用 React 原生 `createContext` 创建 `AuthContext.jsx`（封装 token、tenant_id、entity）与 `TelemetryContext.jsx`（封装 `/ws/metacorp` 事件流）。
2. 将 `web/src/App.jsx`（3,569 行）中内联的 7 个视图逐一剪切为独立组件文件放入 `web/src/views/`：`TasksView.jsx`, `ChatsView.jsx`, `ArtifactsView.jsx`, `ApprovalsView.jsx`, `OrgRolesView.jsx`, `UsageFinOpsView.jsx`, `EvalsExpView.jsx`。确保拆分后 `App.jsx` 控制在 600 行以内。
3. 将 `app/ui/api.py`（2,855 行）中的 79 个端点按领域拆分为 `app/ui/routers/` 下的子模块（`projects.py`, `chats.py`, `tasks_runs.py`, `evals.py`, `orgs.py`），在 `app/ui/api.py` 中通过 `router.include_router()` 汇总暴露，保持对外 HTTP 契约 100% 不变。
* **Verification Cmd**:
```powershell
cd web && npm run build && cd .. && pytest
```
* **DoD**: `npm run build` 零报错产出 `web/dist`，启动 8078 验证副本访问 `/app/` 首屏与全部 14 个视图切换 `pageerror = 0`，全量后端测试 100% 通过。
