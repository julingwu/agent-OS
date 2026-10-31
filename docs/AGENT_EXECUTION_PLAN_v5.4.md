# AGENT_EXECUTION_PLAN: Agent OS (oaagent-m0) v5.3 -> v5.4
## L1 验收闸收口、台账归档与生产库点火

```yaml
meta:
  project: oaagent-m0 (Agent OS)
  baseline: "v5.3 worktree (622 passed / 0 failed)"
  target_version: "5.4.0"
  hard_invariants:
    - "生产模式下严禁 flow_stage_accept 自动将 blocking L1 human_check 置为 passed"
    - "操作生产库 data/oa.db 前必须先生成带时间戳的物理备份文件"
    - "保持 622+ 测试用例 100% 绿灯"
    - "严禁关闭/删除/绕过 SQLite 5 个触发器"
    - "严禁把 uvicorn 改多 worker，保持 SQLite WAL 单写"
    - "严禁修改 web/vite.config.js 的 base: './'"
```

---

## 验收口径（四条规则）

* **规则 1（阶段职责对齐）**：在 `build` 和 `test` 阶段，任务只要 **L0 与 L2 全绿**，即可放行进入第 7 阶段 `accept`
  （任务状态保持 `pending_review`，等待第 7 阶段统一验收）。
* **规则 2（废除伪造签批）**：当流水线进入 `accept` 阶段时，`flow_stage_accept` **严禁自动将 blocking L1 `human_check` 置为 `passed`**。
  若存在未签批的 L1 判据，`accept` 阶段保持 `active`（挂起等待人工验收），并触发通知/企微签名请求。
* **规则 3（人工签批触发收尾）**：当人类调用签批接口（附上 `evidence_snippet`）使所有 L1 判据变为 `passed` 时，
  任务转为 `completed`，并自动唤醒流水线走完剩余的 `release` 与 `retro` 阶段直至项目 `done`。
* **规则 4（存量测试平滑适配）**：原本依赖 `flow_stage_accept` 自动跑通全流程的存量测试，要么显式补一步调用 L1 签批接口
  （传测试 `evidence_snippet`），要么仅在显式开启测试环境变量（`M0_AUTO_SIGN_L1_FOR_TEST=1`，生产严禁开启）时才允许自动过 L1。

---

## SPRINT 6: L1 验收闸真挂起闭环与工程台账收口

### TASK-6.1: 废除 `flow_stage_accept` 自动签批后门，打通"L1 人工真签批 -> 自动出货"闭环

* **Target Files**: `app/pipeline.py`, `app/acceptance_run.py`, `app/api/tasks.py`, `tests/`
* **Context**: 当前非 `web-v1` 的 pack 在 `accept` 阶段由 `flow_stage_accept` 自动将 L1 `human_check` 标为 `passed`，
  绕过人工证据校验；而 `web-v1` 在 `build` 阶段就被 L1 卡在 `pending_review`。

* **Action Steps**:
  1. **统一 `build/test` 与 `accept` 的判据拦截时机**：`build` 阶段任务执行成功且 L0+L2 全 passed 时，
     即便含未签批 L1 `human_check`（`pending_review`），也允许推进至 `test` 与 `accept` 阶段。
  2. **收口 `flow_stage_accept`**：生产路径下进入 `accept` 时检查项目下所有任务的 blocking L1；
     仍有未通过项则**严禁**自动置 `passed`，阶段保持 `active`，发出待验收通知/事件。
  3. 人类通过 `/criteria/{criterion_id}/check`（或 UI / 企微签名页）提交非空 `evidence_snippet` 完成所有 blocking L1 签批后，
     任务 `pending_review -> completed`，并自动触发 `accept` 完成及 `release`、`retro` 执行。
  4. 存量测试：仅在 `M0_AUTO_SIGN_L1_FOR_TEST=1`（生产严禁）时放行自动过 L1，或测试显式调用签批 API。
     新测试 `tests/test_v54_l1_human_gate_strict.py` 严格断言：无人工 `evidence_snippet` 时项目必停在 `accept`，
     补齐签批后自动走完 `release` 并生成 zip 包。

* **Verification Cmd**:
```powershell
.venv\Scripts\python.exe -m pytest -q
```
* **DoD**: 全部测试通过；`web-v1` 与其余 4 个 pack 在 L1 验收闸上行为 100% 统一且严格受人工 `evidence_snippet` 控制。

### TASK-6.2: 更新 `DESIGN_GAPS.md` 台账与架构文档版本同步

* **Target Files**: `DESIGN_GAPS.md`, `app/main.py`, `app/ui/ops.py`, `web/src-tauri/tauri.conf.json`
* **Action Steps**:
  1. 在 `DESIGN_GAPS.md` 尾部续写 §69–§75，记录本轮实证发现的真实工程教训：
     * **§69**: L0 红线扫描的自指陷阱（测量工具 `test_check.py` 必须豁免于被测文案扫描并记入 `skipped_tooling`）。
     * **§70**: 落地页模板与自检脚本的契约对齐（`_render_finops_html` 的 `id="btn-calc"` 缺失教训）。
     * **§71**: 异步后台线程的 `DB_PATH` 寻址漂移（必须在请求入口绑定 `owner_db`）。
     * **§72**: SQLite `PRAGMA journal_mode=WAL` 的排他锁竞态与显式重试。
     * **§73**: `task_runs.runner` 审计归因回写与双内核显式路由契约。
     * **§74**: `flow_stage_accept` 伪造 L1 签批后门的废除与真人工验收闭环。
     * **§75**: 测试捷径 `M0_PIPELINE_EXEC=0` 会绕过整条验收链，用它测出的指标无意义。
  2. 将版本号统一升至 `5.4.0`。

### TASK-6.3: 生产库安全备份、平滑迁移与噪音经验清洗（需人工确认后执行）

* **Target Files**: `data/oa.db`（操作前必备份）
* **Action Steps**:
  1. 物理备份：`data/oa.db`（连同 `-wal` / `-shm`）完整拷贝为 `data/oa.db.bak_v5.3_<时间戳>`，并校验 sha256。
  2. 对 `data/oa.db` 执行幂等 DDL 迁移（补齐 `project_stages` 列、`eval_runs.tenant_id`、`org_experiences.tenant_id` 等）。
  3. 清洗历史乌龙积压：将因"L0 自指陷阱（`test_check.py`）"与"模板缺 `btn-calc`"批量产生的无效 `pending` 经验
     批量审核标为 `rejected`，写审计 `experience_reviewed`（`reason="v5.4_purge_self_ref_noise"`）。
  4. 输出清洗后健康快照（表行数、经验状态分布、项目状态分布）。
