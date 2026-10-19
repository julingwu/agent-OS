# octo-agent-bridge

Octo 是壳（组织 / 群聊 / Bot 注册），底座是 `C:\oaagent-m0` 的 FastAPI 外壳（任务、判据、预算、沙箱、审计）。
桥接层把「群里的一句话」变成「底座的一条任务」，再把结果写回群里。

```
群/DM 消息 ──▶ inbox(MySQL message*)──▶ guard ──▶ 底座 /tasks/from_sentence（HMAC 签名）
                                            │              │
                                            │              ├─ spawn_run → execute（ACP / mock）
                                            │              └─ acceptance/run（L0/L1/L2）
                                            ▼                      │
                                     丢弃/静音 ◀── 预算/链路闸        ▼
                                                            回帖到群（Bot 身份）
```

## 组件

| 文件 | 职责 |
|---|---|
| `app/octo/client.py` | Bot API（send / events / ack）+ 人类 token（建群 / 拉人） |
| `app/octo/inbox.py` | 直接 tail MySQL `message*` 分片，解 JSON，过滤系统号与非文本 |
| `app/octo/guard.py` | 防死循环：链路上限、必须 @、窗口重置、每 Bot 调用预算 → 静音 |
| `app/octo/agent.py` | PM Agent：把大任务拆成角色，建群、拉人、发分工 |
| `app/octo/tools.py` | 暴露给 Agent 的工具：`create_group` / `invite` / `say` |
| `app/octo/runner.py` | 常驻线程：收 → 闸 → 建任务 → 执行 → 回帖 |
| `app/octo/api.py` | `/octo/status`、`/octo/demo/ceo_group`、`/octo/mute/{bot}`、`/octo/artifacts` |

## 关键设计

**为什么不用 `/v1/bot/events`**：开源版里没有任何服务往队列里写消息——只有 `bot_joined_group`
和内部 `POST /v1/internal/bot-mentions` 两个入队点，而后者没有任何随包服务调用
（matter 只调 `/v1/internal/notify`）。实测轮询永远是 `{"results":[]}`。
所以桥接层改读 `message` 分片（payload 是明文 JSON），用每表游标推进。

**为什么建群要用人类 token**：Bot API 明确禁止 Bot 拉其他 Bot 进群
（`createGroup — human members only, cannot add bots`）。PM Agent 的建群工具因此走
董事长账号（`OCTO_HUMAN_TOKEN`），这既满足「Agent 自主拉群」，也保证群主是人类。

**建群必须带 `space_id`**：空间模式下不带 space 的群不进 IM，界面上完全看不到（踩过）。

**权限隔离**：`/octo/artifacts?bot_uid=` 只返回该 Bot 绑定身份自己的 `task_runs` 产物；
前端 Bot 看不到后端 Bot 的代码。实测：`fe` → `article.md`；`be` → `fetch_quote.py` / `prices.csv`。

## 防死循环参数（`.env`）

| 变量 | 默认 | 含义 |
|---|---|---|
| `OCTO_MAX_BOT_CHAIN` | 3 | 同一窗口内一个 Bot 最多回几条 |
| `OCTO_CHAIN_WINDOW` | 600 | 窗口秒数 |
| `OCTO_BOT_MIN_GAP` | 20 | 两条 Bot 回复最小间隔 |
| `OCTO_MUTE_SECONDS` | 3600 | 预算耗尽后的静音时长 |
| `octo_bots.budget_calls` | 200 | 每个 Bot 的调用预算 |

规则：人类说话 → 链路归零、永远放行；Bot 说话 → 必须 @ 本 Bot 且未超链路上限；
预算用尽 → 回一句「静默 1 小时」后不再出声。

## 演示

```bash
# 1. 让 PM Agent 自己建群拉人（人类 token 建群，Bot 身份发分工）
curl -s -X POST localhost:8077/octo/demo/ceo_group \
  -H "X-Octo-Token: $OCTO_SIM_TOKEN" -H "content-type: application/json" \
  -d '{"text":"双十一大促：做一个商品落地页，再写一段营销文案","human_uid":"00000000000000000000000000000000"}'

# 2. 在 Octo 界面里，于该群发一句话 —— 桥接层会建任务、执行、把结果与验收回帖
# 3. 看状态
curl -s localhost:8077/octo/status -H "X-Octo-Token: $OCTO_SIM_TOKEN"
```

## 已验

- 群由 Agent 创建并在界面出现，成员 = 董事长 + 小文 + 前端小前（截图 `docs/octo_group_demo.png`）
- Bot 以 Bot 身份发分工（截图同上）
- 人类消息 → 任务 `T-474dad503633435d` → 执行 640 tokens → 产出 `article.md` 1888B → 回帖验收（`docs/octo_group_live.png`）
- 防循环：3 条 @ 消息处理、第 4 条被链路上限丢弃、无 @ 的直接丢弃（handled 3 / dropped 7）
- 权限隔离：fe/be 两个 Bot 各自只看到自己身份的产物

## 真模型（已验证）

`OCTO_RUNNER_ENGINE=acp`、`OCTO_TASK_BUDGET=80000`、`M0_SANDBOX=lowil`。
实测一条「双十一落地页」请求：3 次运行 51,198 / 50,978 / 21,836 tokens，
产出 36,288 字节 `index.html`（AURUM 三档价格卡），4 条自动判据全过，剩 1 条人工。
预算闸两次生效：30k cap → 44,871 熔断；45k cap → 50,978 熔断（详见 DESIGN_GAPS §45）。

## 未做
- Bot 之间的工具级协作协议（现在 Bot 只回一句确认，不互派任务）
- 多群并发时的优先级与配额
