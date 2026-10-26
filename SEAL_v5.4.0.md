# Agent OS (oaagent-m0) v5.4.0 封版记录

- 基线：v5.4 冻结树，`629 passed / 0 failed / 1 warning`
- 标签：`v5.4.0`（轻量标签，未 push）
- 生成时间：2026-10-26

## 一、运行时物理熔断（`M0_AUTO_SIGN_L1_FOR_TEST`）

自动签批的唯一合法场景被收窄为**双因子同时成立**，缺一即 `RuntimeError` 熔断：

1. 进程内存在**真实的** `pytest` 模块对象（`sys.modules["pytest"] is _REAL_PYTEST`）；
2. 本次自动签批真正写入的库**不是**仓库内生产库 `data/oa.db`。

判定用的是连接的真实文件（`PRAGMA database_list` 的 `main`），不是 `DB_PATH` 环境变量——
后台线程里 `DB_PATH` 可能已经漂移（见 `DESIGN_GAPS.md` §71）。内存库永远判定为非生产库。

条件不满足时**抛异常而非静默返回 False**：静默降级会让运维以为开关生效、代码以为没生效，
两边都拿不到真信号。

实现：`app/acceptance_run.py` 的 `auto_sign_for_test_enabled() / _sign_target_is_production() /
_main_db_file()`；三条断言在 `tests/test_v54_l1_human_gate_strict.py`。

## 二、生产库经验清洗（21 → 17 pending）

清洗前：`approved 12 / pending 21 / rejected 432`（合计 465）
清洗后：`approved 12 / pending 17 / rejected 436`（合计 465，**只 UPDATE 不 DELETE**）

判定规则（按物理证据，不按 id 硬编码）：
`state='pending' AND kind='rejection'` 且该 `task_id` 在 `acceptance_criteria` /
`task_runs` / `artifacts` 上均为 0 条 —— 即驳回结论已无法被任何判据或产物复核。

被清洗的 4 条（每条写入 `ai_audit_logs`，`action='experience_reviewed'`,
`reason='v5.4_purge_empty_no_artifact'`，payload 保留原 title 与 content sha256）：

| id | task | criteria / runs / artifacts |
|---|---|---|
| EXP-170ada2632f847d0 | T-8aa35a06c39a4534 | 0 / 0 / 0 |
| EXP-4966276e40b64ec7 | T-ee25f64deb32473d | 0 / 0 / 0 |
| EXP-74909f460df04a63 | T-3b408ebf835242db | 0 / 0 / 0 |
| EXP-0d4c5ab380b64580 | T-129961137dd54a70 | 0 / 0 / 0 |

保留的 17 条（待人工在控制台审阅转正）：15 条 `workshop` 研讨会共识 +
2 条 `T-2ffcd1e8e4de4ca8` / `T-87bf7aa1b92b45e6` 的 `calc.py` 真实交付失败
（各有 5 条判据、1 次运行记录、0 个产物 —— 判据说的是"没有可检查的产出文件"）。

**如实说明**：这 4 条并非内容为空——它们保留了当时的红线/字数驳回原文；
缺的是**证据链**（判据、运行、产物全为 0 条），因此无法复核。驳回依据是「不可复核」，
不是「信息熵为 0」。原始内容可通过备份或 `ai_audit_logs.payload.content_sha256` 追溯。

备份：`data/oa.db.bak_v5.4_seal_20261026_1029`
- sha256 `9b5c38de6b4112763a19f64d726ddc8f1a9e121db3821726d69cb0477413d1d0`
- 备份前源库 sha256 `24a65c6c4b7eb6940764adc11b3f4c4a7899d155aa0e5407cb3071e264dcdee1`
- 清洗后生产库 sha256 `39b0b64677cc2a843a07aa15ca4790b0d7459e7bff52f9dbb82766eaeed6c6f3`
- 三校通过：表数一致 / `org_experiences` 行数一致 / `tasks` 行数一致 / `quick_check=ok`

## 三、Git 原子提交

提交前做过全量 Secret 预检（97 个待入库文件，64 位 hex / 企微 Token / AESKey /
`sk-` / Bearer / 私钥块等模式，**0 命中**）。`data/`、`.env`、备份、一次性补丁脚本
均在 `.gitignore` 内，未入库。

| 提交 | 主题 | 文件数 |
|---|---|---|
| `de560ba` | feat(v5.1) 赛博指挥舱 / 路由模块化 / MCP / 遥测 | 50 |
| `48b663b` | fix(v5.2) 地基止血 | 26 |
| `2cf3b6b` | feat(v5.3) NativeKernel / eval 租户 / 局部恢复 / 并发加固 | 12 |
| 本次 | fix(v5.4) L1 人工闸门封版 | — |

**归档口径（重要）**：v5.1→v5.4 的改动此前长期未提交，磁盘上从未存在过中间态，
因此只能按"文件"归档到最接近的版本，同一文件内的跨版本改动无法再拆。
提交顺序不等于可以逐步 checkout 的构建顺序——只有最终树是经过 629 条用例验证的。

## 四、已知残留

1. `project_stages.tenant_id` 不加：`project_stages` 经 `project_id` 强从属 `projects`，
   而 `projects.tenant_id` 已有隔离，不存在脱离父实体的独立查询路径（决策已锁定）。
2. DoD 指标改为两层口径：自动化送审率 `(awaiting_accept + completed) / total`、
   终态交付率 `completed / total`。v5.3 的 `1.0000` 不再复现，是设计意图而非退化。
3. 15 条研讨共识与 2 条真实失败经验仍为 `pending`，等人工审阅。
