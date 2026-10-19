# 审计时间轴 · T-942a9ecb7cc54da2

- 标题：写一篇 300 字的秋季护肤短文
- 业务包：content-v1　实体：LE-GROUP-HQ　状态：**completed**
- 创建：2026-10-18 15:10:01　更新：2026-10-18 15:12:57
- 运行次数：1　累计 tokens：**8,859**　产物：1

## 判据快照

| seq | acceptance_layer | state | criterion | check_type | note |
|---|---|---|---|---|---|
| 1 | L2 | passed | 产出文件存在、可读且非空 | script_exec | 文件 可用（1047 字节） |
| 2 | L2 | passed | 正文 300-480 字（CJK 字符 + 英文单词，去空白） | script_exec | 字数 301（区间 300-480） |
| 3 | L0 | passed | 命中至少 3 组护肤关键词 | rule_auto | 命中 3/3 个关键词组 |
| 4 | L0 | passed | 不含广告法禁用词（最好/最佳/第一品牌/绝对/100%/根治/治愈…） | rule_auto | 无禁用词 |
| 5 | L1 | passed | 品牌调性：语气亲切、不夸大功效（人工判定，须附原文片段） | human_check | 企微审批通过 202610180001 |
| 6 | L1 | na | 配图版权合规（M0 未启用） | human_check | — |


## 运行记录

| id | run_seq | state | runner | usage_tokens | exit_code | started_at | ended_at | actor_identity |
|---|---|---|---|---|---|---|---|---|
| RUN-39f49bf9a05846af | 1 | succeeded | acp | 8859 | 0 | 2026-10-18 15:10:07 | 2026-10-18 15:10:27 | ID-7f3ed41c41ae45f9 |


## 产物

| run_id | file_path | bytes | created_at |
|---|---|---|---|
| RUN-39f49bf9a05846af | C:\oaagent-m0\workspace\ID-7f3ed41c41ae45f9\T-942a9ecb7cc54da2\article.md | 1047 | 2026-10-18 15:10:27 |


## 预算

| id | identity_id | limit_tokens | used_tokens | limit_amount | used_amount |
|---|---|---|---|---|---|
| B-f0d68fabc9f14fad | ID-7f3ed41c41ae45f9 | 200000 | 8859 | 100000.0 | 0.0 |


## 企微推送

_（无）_


## 事件时间轴

| 时间 | 动作 | 阶段 | 执行者 | 摘要 |
|---|---|---|---|---|

| 2026-10-18 15:10:01 | create_task | system | system | criteria_count=6 |
| 2026-10-18 15:10:07 | state_transition | system | shell | event=run_spawned |
| 2026-10-18 15:10:07 | spawn_run | system | system | RUN-39f49bf9a05846af task_id=T-942a9ecb7cc54da2 run_seq=1 |
| 2026-10-18 15:10:07 | reserve_budget | system | system | reason=None |
| 2026-10-18 15:10:07 | state_transition | system | adapter | event=run_started |
| 2026-10-18 15:10:07 | emit_event | system | system | event=run_started |
| 2026-10-18 15:10:07 → 2026-10-18 15:10:22 | 心跳 ×2 | system | system | 运行存活探针 |
| 2026-10-18 15:10:27 | attach_artifact | system | system | ART-063ba970d6c34a58 art_seq=1 file_path=C:\oaagent-m0\workspace\ID-7f3ed41c41ae45f9\T-942a9ecb7cc54da2\article.md |
| 2026-10-18 15:10:27 | state_transition | system | adapter | event=run_succeeded |
| 2026-10-18 15:10:27 | emit_event | system | system | event=run_succeeded |
| 2026-10-18 15:10:27 | run_executed | system | system | runner=acp, exit_code=0, event=run_succeeded, usage_tokens=8859, artifacts=1 |
| 2026-10-18 15:10:28 | acceptance_run | system | system | verdict=pending |
| 2026-10-18 15:12:57 | manual_check | L1 | ID-da610f30a4e94e0d | C-38879e315e684abc passed=True note=企微审批通过 202610180001 |
| 2026-10-18 15:12:57 | state_transition | system | acceptance | event=review_passed |
| 2026-10-18 15:12:57 | acceptance_run | system | system | verdict=completed |
