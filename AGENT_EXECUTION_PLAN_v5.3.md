# AGENT_EXECUTION_PLAN: Agent OS (oaagent-m0) v5.2 -> v5.3 真生成引擎接入与残余缺口收口

```yaml
meta:
  project: oaagent-m0 (Agent OS)
  baseline: "v5.2 worktree (591 passed / 0 failed)"
  target_version: "5.3.0"
  automated_dod_metric: "(count(pending_review) + count(completed)) / count(total_tasks) >= 0.60"
  hard_invariants:
    - "严禁破坏现有 591 个已通过的 pytest 用例（离线测试默认走 deterministic fallback，不消耗真实 API 额度）"
    - "严禁关闭 SQLite 5 个触发器或绕过 L1 evidence_snippet 强制校验"
    - "严禁污染生产库 data/oa.db"
    - "真 LLM 调用必须实时接入 app/finance.py 与 app/telemetry.py，准确上报 usage_tokens 与 RUN_TRACE_EVENT"
```

---

## SPRINT 4: 补齐残余数据/路由缺口与前端 E2E 冒烟 (半天)

### TASK-4.1: 补齐 `packs` 种子表 `web-v1` 与 `eval_runs.tenant_id` 物理列

* **Target Files**: `app/pipeline.py` (或种子初始化模块), `app/evals.py`, `tests/`
* **Context**:
1. 生产库 501 个任务使用 `pack_id = 'web-v1'`，但 `packs` 种子表中缺失 `web-v1`，存在外键/引用完整性黑洞。
2. `eval_runs` 表缺少 `tenant_id` 列，目前仅靠 `JOIN eval_sets` 过滤，孤儿运行记录在多租户下存在越权或丢失隐患。

* **Action Steps**:
1. 在数据库种子初始化逻辑中幂等插入 `web-v1` pack 定义（包含默认角色绑定、阶段提示词模板、L0/L1/L2 默认判据配置及预算基线）。
2. 为 `eval_runs` 建表 DDL 及启动期幂等 `ALTER TABLE` 迁移补齐 `tenant_id TEXT DEFAULT 'TENANT-HQ'`。
3. 在 `app/evals.py` 创建、查询、删除 `eval_runs` 的所有 SQL 中显式写入并校验 `tenant_id = ?`。

* **Verification Cmd**:
```powershell
.venv\Scripts\python.exe -m pytest -k "pack or eval or tenant" -q
```
* **DoD**: 新库初始化后 `SELECT * FROM packs WHERE id='web-v1'` 存在记录；不同租户创建的 `eval_runs`（含未绑定 `eval_sets` 的孤儿 run）严格按 `tenant_id` 物理隔离。

### TASK-4.2: `/ui/projects/{id}/resume` 支持按阶段 `keys` 精确过滤与局部重置

* **Target Files**: `app/ui/routers/projects.py` (或拆分后的对应路由文件), `app/pipeline.py`, `tests/`
* **Context**: 当前 `/projects/{id}/resume` 会重置所有非 `done` 阶段，缺少按指定阶段 `keys` 过滤的能力，粒度过粗。
* **Action Steps**:
1. 在 `POST /ui/projects/{project_id}/resume` 请求体（或 Query 参数）中增加可选参数 `stage_keys: Optional[list[str]] = None`。
2. 当传入 `stage_keys` 时，仅对匹配 `stage_key IN (...)` 且状态非 `done` 的阶段执行状态重置与任务续跑（内部继续保持 TASK-3.2 的成果：阶段内已 `completed` 的任务绝不重复执行）。
3. 当未传入 `stage_keys` 时，保持原有行为以兼容旧调用方与现有测试。

* **Verification Cmd**:
```powershell
.venv\Scripts\python.exe -m pytest -k "resume or heal" -q
```
* **DoD**: 新增针对 `stage_keys=['build']` 的单元测试，验证仅 `build` 阶段被唤醒续跑，其余非 `done` 阶段状态不被误重置。

### TASK-4.3: 拆分后前端 14 视图的真实浏览器渲染冒烟测试

* **Target Files**: `tests/test_v53_ui_views_smoke.py` (或现有 UI 测试套件), `web/src/views/*`
* **Context**: `App.jsx` 已从 3,569 行拆分为 512 行 + 7 个独立视图组件，目前仅通过了 `vite build` 静态编译，尚未在浏览器运行时验证全部 14 个视图切换是否零报错。
* **Action Steps**:
1. 编写自动化冒烟测试（优先使用仓库已有的浏览器测试工具如 Playwright，若无则通过 TestClient 验证静态产物引用完整性 + 组件导入契约）：依次加载 `/app/` 首屏（赛博指挥舱）并触发切换全部 14 个视图。
2. 断言切换过程中 `console.error` 与 `pageerror` 计数为 0，無 `undefined is not a function` 或 Context 丢失导致的白屏。

* **Verification Cmd**:
```powershell
cd web && npm run build && cd .. && .venv\Scripts\python.exe -m pytest -k "ui or smoke" -q
```
* **DoD**: 14 个视图全部可正常挂载渲染，`pageerror = 0`。

---

## SPRINT 5: `NativeKernel` 真 LLM 任务化生成改造 (核心物理瓶颈突破)

### TASK-5.1: 将 `NativeKernel` 从固定模板吐出器升级为“真 LLM 生成 + 离线模板双模驱动”

* **Target Files**: `app/engine/runner_native.py`, `app/acceptance/l2_scripts.py`, `tests/test_v53_native_llm_generation.py`
* **Context**: 目前 `runner_native.py` 对所有任务均吐出同一套 6 个文件的 FinOps 骨架（仅标题参数化），导致做“秋季护肤落地页”产出的也是 FinOps 计算器代码。必须在**不破坏现有 591 个离线单元测试**的前提下，接入真实的 DeepSeek / OpenAI 兼容 HTTP API (`MODEL_BASE_URL` / `DEEPSEEK_API_KEY`)。
* **Action Steps**:
1. **双模开关设计（保护测试确定性）**：
* 在 `runner_native.py` 中判定运行模式：当 `M0_NATIVE_MODE == 'llm'`（或配置了真实有效的 `DEEPSEEK_API_KEY` 且未处于 pytest mock 模式 `M0_MOCK_LLM=1`）时，进入 **Live LLM Generation Loop**；
* 在 pytest 默认环境（或 `M0_NATIVE_MODE == 'template'`）下，继续走现有的确定性 6 文件模板分支，确保 591 个存量测试秒级通过且零外部网络依赖。
2. **Live LLM Generation Loop 实现**：
* **组装上下文**：读取当前任务的 WBS 描述、所属 `pack` 规范、上游阶段写入 `project_artifacts` 黑板的结论（截取上限内摘要）、以及 `org_experiences(mandatory=1)` 的董事长红线与召回的 `approved` 经验。
* **受控工具循环**：向 `MODEL_BASE_URL` 发起 Chat Completions 请求（支持 `write_file` / `read_file` / `run_script` 工具定义，严格受 `TASK-1.2` 的工具调用上限与 `lowil.py` 沙箱目录约束）。
* **动态产物与自检对齐**：要求模型根据实际业务需求（如“秋季护肤落地页”或“竞品分析报告”）生成真实匹配的 HTML/CSS/MD/Py 产物，并由系统根据该任务的 `acceptance_criteria` 生成匹配的 `test_check.py`（而非写死检查 FinOps 的 `id="btn-calc"`，除非任务本身就是计算器）。
* **真实成本与遥测记账**：从 LLM HTTP 响应的 `usage` 字段读取真实的 `prompt_tokens` 与 `completion_tokens`，实时写入 `task_runs`、触发 `ACP_USAGE_UPDATE` / `RUN_TRACE_EVENT` 并执行 80% 阶段预算熔断检查（`budget_pause`）。
* **Verification Cmd**:
```powershell
.venv\Scripts\python.exe -m pytest -q
```
* **DoD**:
1. 新增 `test_v53_native_llm_generation.py`（通过本地 Mock HTTP Server 模拟 `MODEL_BASE_URL` 返回护肤品落地页代码与真实 token usage），验证 `NativeKernel` 走通完整 LLM 工具调用循环，产出的 HTML 内容与任务主题 100% 匹配，不再出现 FinOps 残留字样；
2. 全量测试（591 + 新增用例）100% passed。
