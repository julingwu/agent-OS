# Agent OS (oaagent-m0) v5.4.2 补丁记录

- 基线：v5.4.1（tag `v5.4.1` = `1ea5fdd`，工作区干净，全量 `631 passed`）
- 目标：`649 passed / 0 failed`（v5.4.1 的 631 + 本补丁新增 18 条：线程库上下文 9 条 + 阶段 overhead 9 条）
- 标签：`v5.4.2`

本补丁只做两件事：**把后台线程的库上下文钉死并加测试排空屏障**（TASK-1，P0），
**把 `llm.complete()` 12 处调用点的真实 token 记进阶段预算与 FinOps 报表**（TASK-2，P1）。
两件事都直接对应 v5.4.1「已知不稳定」那两条偶发失败与 §77 的「仍未解决」。

---

## 一、TASK-1：`_wake_pipeline` 后台线程的跨用例竞态（P0）

### 现象

v5.4.1 三次全量里 1 次出现 2 failed，报错都是 `no such table: task_runs`：

- `test_runner_artifact_collection.py::test_runners_register_all_fresh_deliverables_but_only_known_partial_files[True-acp]`
- `test_runner_start_guards.py::test_killed_queued_run_never_reserves_or_spawns_but_new_retry_is_allowed[failed-mock_acp]`

单跑这两个文件 29 条全绿 —— 跨用例后台线程竞态。

### 根因（两层）

1. 线程到**执行时**才读库位置：`run_project` 内部深层仍走 `get_conn()` →
   `connect()` → `db_path()` → 重读 `DB_PATH`。用例 A 的线程活到用例 B 时读到 B 的库
   或一个已删除的 tmp 路径。
2. SQLite 对不存在的文件**静默新建 0 表空库**，于是「库不是我的了」被降级成业务层
   的 `no such table`。

### 实测证据（不是推测）

在 v5.4.1 tag 的干净 worktree（`C:\oaagent-probe-wt`，无 `.env`、无 `data/`）上挂
 pytest 插件，对每个用例的 setup/teardown 抓 `threading.enumerate()`：

```
631 passed, 1 warning in 409.35s (0:06:49)
[probe] snapshot events=1262 events-with-extra-threads=260
```

在**下一个用例 setup 时仍活着**的线程（即真正跨用例泄漏的）：

| 线程名 | 泄漏现场 |
|---|---|
| `oa-pipe-P-ce3c2440b5c0` | 在 `test_v50_project_creation_and_ux` 创建，活到 `test_v50` 下一条 + `test_v51_metacorp_cyber_and_mcp` 的 3 条 |
| `retry-RUN-50b7ee1a60114943` | 活到 `test_runner_start_guards.py::test_killed_queued_run...[failed-mock_acp]` 的 setup —— 正是 v5.4.1 失败的那条参数 |
| `oa-notify` | 活到 `test_v43_budget` 的 6 条 |
| `hb-RUN-405605279a6b41e5` | 心跳线程，活到后续 3 条 |
| `Thread-194 (push_approval)` | 活到 `test_m18_approval_resume` 的下一条 |

### 修法（四层，缺一层都不算根治）

1. **优先级下沉到 `db_path()` 本身**：线程绑定 > `DB_PATH` > 默认生产库。
   `threading.local` + `pin_thread_db(path)` + `current_thread_db()`。
   `connect()` / `open_conn()` / 任意深度 caller 全部继承。
2. **绑定态连接防御**：绑定态用 `file:...?mode=rw`（`uri=True`）打开并校验核心表
   （`tasks` / `task_runs` / `project_stages`）；文件被删或库未初始化 →
   `BoundDbUnavailable` + `db.bound_db_failures()` 留证，线程干净退出。
   **非绑定态行为不变**（`init_db()` 依赖 `connect()` 建新库）。
3. **统一派发口 `db.spawn_bg_thread(target, name=, db=)`**：登记 `_active_bg_threads`
   + 线程内 `pin_thread_db` + `finally` 注销。
4. **测试生命周期屏障**：`tests/conftest.py` 在 `yield` 之后、`DB_PATH` 断言之前
   `wait_for_bg_threads(timeout=10.0)`，排不空直接 fail。

### 改动文件

| 文件 | 改动 |
|---|---|
| `app/db.py` | 线程绑定（`pin_thread_db` / `current_thread_db`）、`db_path()` 优先级、绑定态 `mode=rw` + 核心表校验、`BoundDbUnavailable` + `bound_db_failures()`、`spawn_bg_thread()` / `active_bg_threads()` / `wait_for_bg_threads()` |
| `app/pipeline.py` | `_wake_pipeline()` 改走 `spawn_bg_thread` |
| `app/ui/routers/projects.py` | 立项点火 / 续跑两处线程改走 `spawn_bg_thread` |
| `app/ui/dispatcher.py` | 异步派活线程改走 `spawn_bg_thread` |
| `app/ui/routers/tasks_runs.py` | 设计确认后的开发线程、重试 worker 改走 `spawn_bg_thread` |
| `app/rbac.py` | 审批卡推送线程改走 `spawn_bg_thread` |
| `app/notify.py` | 通知投递线程改走 `spawn_bg_thread` |
| `app/engine/dsh_adapter.py` | 心跳线程绑定 owner 库（只绑定、不登记，见第四节） |
| `tests/conftest.py` | teardown 排空屏障 |
| `tests/test_v542_thread_db.py` | 新增 9 条回归（见下） |

---

## 二、TASK-2：`complete()` 12 处调用点的 FinOps 记账闭环（P1）

### 现象

§77 已记录：`complete()` 能拿到 usage，但**没有落到任何账本**。立项书 / 研讨 /
WBS / 复盘 / 阶段文档（12 处调用点）在 FinOps 上一律记 0，也不进阶段 80% 预算检查。

### 根因（三个断点）

1. sink 是模块全局变量 `_usage_sink`，多线程互相覆盖；
2. 调用方（`pipeline_llm.*` / `flows.*`）没有 `conn`，也不知道自己处在哪个项目/阶段；
3. `finops.overview()` 只按 `task_runs.usage_tokens` 聚合，阶段 overhead 记了也看不见。

### 修法（复用现成积木，不另造表）

1. `app/llm.py`：sink 改 `contextvars.ContextVar`（`usage_sink()` 上下文管理器 +
   `current_usage_sink()`，`set_usage_sink()` 保留兼容）。实测 Python 新线程是空上下文，
   主线程 sink 不会漏进子线程。
2. `app/finance.py`：`StageUsageMeter` + `record_stage_overhead()` + `overhead_scope()`。
   落账累加 `stage_budgets.spent` / `project_budgets.spent_tokens`（与 `spend()` 同口径，
   所以 `quota()` / `pause_threshold()` 的 80% 检查天然覆盖），并写
   `budget_events(kind='stage_overhead')`。
3. `app/pipeline.py`：`run_stage()` 整体包 `finance.overhead_scope(conn, pid, key, tenant_id)`，
   原实现改名 `_run_stage()`。
4. `app/ui/dispatcher.py`（pm_summary）、`app/ui/routers/tasks_runs.py`（run 诊断）
   两处没有阶段上下文的调用按**项目级 overhead**（`stage_key=None`）入账。
5. `app/finops.py`：`overview()` 显式分列 —— `totals.tokens = task_tokens + overhead_tokens`，
   新增 `overhead.by_stage`；`by_agent` / `by_model` 保持纯任务口径并在 docstring 写清。

### 12 处调用点的归属（实测清单）

| # | 调用点 | 归属 |
|---|---|---|
| 1 | `flows.classify`（意图路由） | 立项前无 `project_id` → **静默不入账**（不报错、不写脏账） |
| 2 | `flows.pm_summary` | 项目级 overhead（`stage_key=None`） |
| 3 | `flows.diagnose` | 项目级 overhead（`/ui/runs/{id}/diagnosis` 入口注册） |
| 4 | `flows.stage_document` | 所在阶段（`run_stage` → `_flow_execute`） |
| 5-11 | `pipeline_llm.intake/charter/workshop_statement/minutes/wbs/release/retro` | 所在阶段 |
| 12 | `evals` 生成 | 无 `project_id` → **静默不入账** |

即：**10/12 精确归因**，2 处按要求安全忽略。

### 一个关键取舍（写清，免得下一轮当 bug 查）

规约建议「sink 回调里用 `open_conn(owner_db)` 短连接写库」。实测这条走不通：
`complete()` 是在阶段主事务**进行中**被调用的（研讨会先 `INSERT workshops` 再调模型），
此时另开连接写库会撞主事务写锁（`SQLITE_BUSY` / 30s `busy_timeout`），既慢又丢账。
所以 `StageUsageMeter` 只在内存里线程安全累加，阶段边界上用**同一个 `conn`** 落账：
成功路径自己 commit；异常路径只写不 commit，由调用方事务决定去留（与阶段半成品产物
同一语义）。记账异常一律吞掉。

### 改动文件

`app/llm.py`、`app/finance.py`、`app/pipeline.py`、`app/finops.py`、
`app/ui/dispatcher.py`、`app/ui/routers/tasks_runs.py`、`tests/test_v542_stage_overhead.py`

---

## 三、新增测试（18 条）

- `tests/test_v542_thread_db.py`（9）：
  `test_db_path_priority_binding_over_env`、`test_background_thread_get_conn_inherits_pinned_db`、
  `test_background_thread_inherits_even_if_env_moves`、`test_bound_thread_refuses_missing_database_file`、
  `test_bound_thread_refuses_uninitialized_database`、`test_unbound_connect_still_creates_database`、
  `test_spawned_worker_dies_cleanly_when_db_removed`、`test_spawn_registers_and_waits_for_threads`、
  `test_wait_for_bg_threads_noop_when_idle`
- `tests/test_v542_stage_overhead.py`（9）：
  `test_usage_sink_is_context_local_not_global`、`test_usage_sink_context_manager_restores_previous`、
  `test_sink_exception_never_blocks_generation`、`test_charter_complete_tokens_land_in_stage_budget`、
  `test_workshop_complete_tokens_are_all_captured`、`test_pause_threshold_sees_stage_overhead`、
  `test_project_level_overhead_without_stage`、`test_no_project_context_is_safely_ignored`、
  `test_finops_overview_splits_task_and_overhead`

---

## 四、与规约不一致的地方（逐条）

1. **写作用域扩大**：规约列 `app/db.py`、`app/pipeline.py`、`projects.py`、`tests/conftest.py`。
   实测探针显示泄漏还来自 `dispatcher.py`、`tasks_runs.py`、`rbac.py`、`notify.py`、
   `dsh_adapter.py`，一并收口。理由：只修规约列的 3 个派发点，`retry-RUN-*` 那条泄漏
   仍然存在 —— 而它恰好就是 v5.4.1 失败参数的上游。
2. **sink 不直接写库**：见第二节「关键取舍」。
3. **`evals` 生成与 `flows.classify` 不入账**：规约允许「安全忽略或记租户级」，选忽略。
4. **心跳线程只绑定不登记**：kill/超时类用例里 run 线程本就可以比用例长寿，登记进
   排空屏障会让 teardown 白等甚至超时；绑定已足够防止写错库（库被回收时
   `BoundDbUnavailable` 被心跳自身的 `except` 吞掉）。
5. **未收口的派发点（如实列出）**：`chats.py` 的 `_run_turn` / `_run_group_turn`、
   `evals.py` 的评测线程、`resources.py`、`approval_flow.py` 的 resume、`governance.py`
   的重试、`ops.py` 的 ticker。本轮探针未观察到它们跨用例泄漏（清单见第一节表），
   但同一类风险仍在，下一轮应统一迁移到 `spawn_bg_thread`。

---

## 五、验证（原始输出尾行）

| 项 | 原始尾行 |
|---|---|
| v5.4.1 基线（干净 worktree + 探针） | `631 passed, 1 warning in 409.35s (0:06:49)` |
| 全量 round 1 | `649 passed, 1 warning in 401.55s (0:06:41)` |
| 全量 round 2（紧接 round 1） | `649 passed, 1 warning in 370.47s (0:06:10)` |
| 循环 `test_runner_artifact_collection` ×10 | `10 passed` ×10（10/10，单轮 5.85–12.90s） |
| 循环 `test_runner_start_guards` ×10 | `19 passed` ×10（10/10，单轮 13.87–16.75s） |
| tag 干净 checkout 全量（无 `.env`、无 `data/`） | `649 passed, 1 warning in 371.41s (0:06:11)` |

注：干净 checkout（`git worktree add --detach C:\oaagent-probe-wt v5.4.2`，root 里没有
`.env`、没有 `data/`，`git status` 干净）那轮跑在提交 `cf00ebd` 上；之后只把本节
验证数字补进本文件并 `git commit --amend`，**代码零改动**，tag 已重新指向 amend 后的
提交。amend 前的全量数字与 amend 后逐字一致（仅文档/版本字符串差异）。

`v5.4.1` 记录的「已知不稳定」两条用例：2 轮全量 + 20 次循环 + 1 轮干净 checkout
累计 **0 次复现**。这不证明竞态在数学上消失，但证明**本套件层面**泄漏源已被屏障挡住。

---

## 六、生产库

`data/oa.db` sha256 前后一致，未写入：

```
CDF3014DA0985ECBD46962F5088E7814B8DCC6A99F25B3122ACAF6B43698CF81
```

---

## 七、未闭环项（如实记录）

1. 12 处调用点里 2 处（立项前意图路由、evals 生成）仍不入账 —— 无 `project_id`，
   要做只能上租户级账本，本轮未做。
2. overhead 只能按 `(project_id, stage_key)` 归因；`by_agent` / `by_model` 仍是纯任务
   口径，报表上不能把这两个维度的和当总数。
3. 第五节第 5 条列出的 6 个派发点未迁移到统一派发口（同类风险残留）。
4. 线程绑定是**显式**的：任何新代码直接 `threading.Thread(...)` 仍会漂移，
   没有「自动继承父线程库」的机制（Python 线程不继承 contextvars，除非手工传递）。
   这条只能靠规范 + review 守住。
