# Agent OS (oaagent-m0) v5.4.1 补丁记录

- 基线：v5.4.0（tag `v5.4.0` = `823d9c0`）
- 目标：`631 passed / 0 failed`（v5.4.0 的 629 + 本补丁新增 2 条用例）
- 标签：`v5.4.1`

## 一、统一 LLM 离线阀门（`app/llm.py`）

**问题（v5.4.0 已暴露但未修）**：`M0_MOCK_LLM` 只在 `runner_native._native_mode()` 里生效，
`app/llm.py` 的 `chat()` 与 `complete()` **都没有阀门**——只要环境里有 key 就真发 HTTP。
配合 `app/main.py` 在 import 时调 `config.load_env()`（`os.environ.setdefault` 把本机 `.env`
灌进进程环境），后果是：某条用例真的调了一次 DeepSeek，且「测试是否通过」取决于这台机器
有没有 `.env`。

**补丁原文的前提需要更正**：补丁写的是「补齐与 `chat()` 完全一致的 `M0_MOCK_LLM` 短路判定」，
但 `chat()` 当时同样没有短路判定。所以做的是**给两个函数装同一个阀门**，而不是让
`complete()` 去对齐一个不存在的实现。

阀门判定（优先级与 `runner_native._native_mode()` 一致）：

```
显式 M0_LLM_MODE（off|live）  >  M0_MOCK_LLM=1  >  有 key 才联网
```

- `is_offline()` 为真时，`chat()` 与 `complete()` 一律 `return None`，**零 HTTP**。
- 要真联调必须显式 `M0_LLM_MODE=live`（两条真 HTTP 用例已按此改写）。

## 二、`complete()` 的 usage 黑洞

`complete()` 以前把 response 里的 `usage` 整个丢掉，所有走它的路径（意图分类、立项书 /
研讨 / WBS / 复盘文档，共 12 处调用点）在 FinOps 上一律记 0。

现在：`complete_with_usage()` 返回 `(text, usage)`，`complete()` 保留原签名、成功时
`_record_usage()` 累计；需要记账的调用方用 `set_usage_sink(fn)` 注册接收器。

**仍未闭环（如实记录）**：这些 usage 目前只进进程内累计，**没有落到 `task_runs.usage_tokens`**
——调用方手上没有 run_id，项目级开销无处记账。要真正闭环需要一个项目/阶段级 overhead 账本，
本补丁没做。

## 三、经验标题的注入套娃（`app/experience.py`）

**问题**：召回的经验被拼成「… 【组织经验（自动注入）】 - [决策否决] …」塞进下一轮 prompt，
而任务标题又是从这个 prompt 截出来的。于是经验标题带着上一轮的注入尾巴，下一轮再抄一遍，
单调增长。

**修法**：剥离放在**唯一写入口** `experience.add()`（不依赖各 caller 自觉），人工编辑入口
`experience.edit()` 同样处理：

- `strip_injected_title()`：截断到第一个注入标记之前；
- `strip_injected_content()`：挖掉「标记 → 同一段内 `《…》` 闭合」的整段，
  保留「未通过判据：…」这类有用信息（直接截断会把有用信息一起丢掉）。

标记清单含历史半截形态（`【组织经验（自动注入)`）与 `【项目上游已交付结论与上下文】`。

## 四、生产库存量清洗（实测结果与补丁预期不同）

补丁预期「17 条 pending 与 12 条 approved 里可能有脏标题」。**实测：这两类各 0 条**。
全库 465 条中只有 3 条 `rejected` 带尾巴，且正是 v5.4.0 已驳回的那批。

处置：只截断这 3 条的 `title`，**不动 content** —— v5.4.0 的审计 payload 里存了
`content_sha256`，改正文会让那份审计失真。每条写
`ai_audit_logs(action='experience_title_scrubbed')` 并保留 `original_title`。

- 备份：`data/oa.db.bak_v5.4.1_20261026_1158`（`445cb082…`，`quick_check=ok`、行数一致）
- 清洗后：仍带尾巴 0 条；`org_experiences` 465 行不变；`approved 12 / pending 17 / rejected 436` 不变

## 五、验证

| 项 | 结果 |
|---|---|
| 全量 pytest（第 2 次） | `631 passed, 1 warning in 415.45s`，exit 0 |
| 全量 pytest（第 1 次） | `629 passed / 2 failed` —— 见下方「已知不稳定」 |
| 新增用例 | `tests/test_v541_llm_gate_and_title_scrub.py`（2 条） |

**已知不稳定（本次实测，未修）**：3 次全量里有 1 次出现 2 条失败
（`test_runner_artifact_collection.py`、`test_runner_start_guards.py`），单跑这两个文件
29 条全绿，属**跨用例的后台线程竞态**：前面的用例留下的守护线程在 `tmp_path` 被删除后重连，
`sqlite3.connect()` 会新建一个空库，于是 `no such table: task_runs`。
大概率来自 v5.4 引入的 `pipeline._wake_pipeline()` 守护线程。
建议下一轮给后台线程连接加 `mode=rw`（库不存在就报错而不是新建），或用 fixture 收敛线程。
这条不修，就不能说「631 全绿」是稳定的。
