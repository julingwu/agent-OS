# 常驻 AI 团队（Grok Bot 式）自建方案 · V0.3

> 状态：**设计稿，待拍板**（不动代码）
> 作者：Agent（Team Lead）｜日期：2026-10-26
> 前提：Linux 服务器；模型走 API 按量付费；不订阅 SuperGrok/Cursor；在现有 `C:\oaagent-m0`（AgentOS 5.4.2）基础上演进

---

## 0. 一页纸结论

**能做成。但不是从零做 —— 你已经有 70%，缺的是"常驻化"和"入口体验"。**

| 问题 | 结论 |
|---|---|
| 要重写吗 | 不用。复用执行/验收/预算/审批/审计五块底座，重写的是**运行时形态** |
| 最大的技术障碍 | 不是 agent 能力，是**没有队列 + 单进程架构**（SQLite 单写者 / 进程内子进程句柄 / lifespan 全局线程 / 重启不清理孤儿），见 2.2、2.5 |
| 最贵的零件 | 不是服务器，是 **token**（但按量付费仍远低于订阅制） |
| 最大的隐形工作量 | **Bot 的人格/模型/工具权限现在根本没有执行入口**（soul 无人读、model 不传 run、tool_acl 空实现），见 2.4 |
| 真正的门槛 | Linux + 每 Bot 容器化隔离 + 持久浏览器会话，三件事一个都不能少 |
| 工期（1 人全职） | 约 6–9 周；其中 P1「关掉页面还在干」1–1.5 周 |
| 月成本 | 服务器 ¥400–700 + token ¥50–300 ≈ **¥450–1000/月**，显著低于 Cursor Ultra（$200≈¥1450） |
| 省的不只是钱 | token 按量付费 + 缓存命中 + 空闲时段半价，重度用也比订阅制便宜；且**额度无墙、数据自主、可定制** |

**三条红线级设计决策（第 11 节展开）**
1. 存储从 SQLite 换 Postgres（否则永远只能单进程）
2. 拆 `web`（无状态可多开）/ `kernel`（单实例有状态）/ `worker`（无状态可扩）
3. Bot 的"电脑"从「任务级目录」升级为「Bot 级持久卷 + 持久 Chrome profile + 容器隔离」

---

## 1. 目标体验：把"像 Grok Bot"翻译成可验收的契约

不写"要有 AI 团队感"这种没法验收的话。每条体验给一条**验收测试**，做到就是做到。

### E1 · 关掉页面它还在干
**用户动作**：手机上派一句"把这周三篇稿子按品牌规范改一遍"，锁屏走人。
**期望**：2 小时后企微收到卡片"3/3 完成，2 篇需你签字"；点开能看产物。
**验收**：派活后 `systemctl restart agent-os`，任务不丢，重启后自动续跑并在企微补发结果。
**现状**：服务器不关的话能跑，但是**同步执行 + 没有队列 + 重启不清理孤儿**，而且生产模板里常驻线程默认全关（见 2.5）。要补的是：队列、租约、孤儿对账、主动推送。

### E2 · 多 Bot 互派任务、群里自动协作
**用户动作**：在群里说"上双十一落地页"。
**期望**：首席 Bot 拆 DAG → 派给前端 Bot / 文案 Bot → 两者并行 → 前端把"需要主图"回派给设计 Bot → 全部完成后首席汇总报你。
**验收**：一次任务里至少出现 3 次 Bot→Bot 的 `assign/result` 事件，且 DAG 图上能画出流转（你已有 `trace.py`）。
**现状**：Bot 只能"回帖"，不能互派（`docs/octo_bridge.md:86` 自己写了未做）。控制台群已有 4 hops/600s 的跳数闸（`store.py:234-252`），可在其上扩展。

### E3 · 它能自己开浏览器把网页上的活干完
**用户动作**："去后台把这 5 个商品的价格改成 199，改完截图给我。"
**期望**：它自己打开浏览器、登录态还在、点进去改、截图、回来报告；中途遇到短信验证码会停下来问你。
**验收**：跨任务登录态保持（关掉再派新任务不用重新登录）；遇到验证码/权限不足时产出 `needs_human` 事件而不是死循环。
**现状**：有 `vendor/chrome-octo` + `tools/cdp_*.py` 的 CDP 能力，但**没有持久登录态**，也没接进 agent 工具循环。

### E4 · 看一遍就学会，之后定时自动跑
**用户动作**："每周一早上 9 点，把上周的管道数据从 CRM 拉出来做成周报发我。"
**期望**：你带它做一遍 → 它问你"要存成每周自动跑吗" → 之后每周一自动产出 + 异常告警。
**验收**：录制一次 → 生成带参数的 skill → 改一个参数（比如日期）能重跑 → 错过时间点（服务器维护）后能补偿执行。
**现状**：有 `scheduled_jobs` + 定时 ticker，但它只有一个 `HH:MM`、一天一次，而且实际执行**忽略存的 prompt**、固定跑内置 `daily_digest`（`app/ui/ops.py:182-192`），无重试无补偿。**没有从执行轨迹学成 skill 的能力**，这是全新模块。

### E5 · 手机随时派活
**用户动作**：地铁上语音/打字派活，看进度，点"批准"。
**期望**：不装新 App 也能用（先用企微 + PWA），以后想升级再打包。
**验收**：手机端能完成"派活 → 看进度 → 签字 → 看产物"四件事，且没有电脑在线。

---

## 2. 现状盘点与结构性障碍

### 2.1 已有资产（可直接复用，不要重写）

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 任务/运行/阶段状态机 | `app/state_machine.py`、`app/models.py` | 直接在队列任务上复用 |
| 真模型执行 + ACP 协议客户端 | `app/engine/acp_client.py`、`runner_acp.py`、`docs/acp-protocol.md` | 执行内核不变，只换宿主进程 |
| 预算闸（池/80% 暂停/追加审批） | `app/finance.py`、`app/finops.py`、V4.3.0 | 队列层直接挂 pre-run reserve / post-run settle |
| 验收判据 L0/L1/L2 | `app/acceptance/` | skill 重放的验收直接绑判据 |
| 审批（企微原生）+ 签字页 | `app/wecom/approval.py`、`sign_page.py` | 手机端"签字"零成本 |
| 审计 / RBAC / 租户 | `app/audit.py`、`app/rbac.py`、`app/tenancy.py` | 审计/租户可直接复用；**但工具级 ACL 是空字段（`positions.tool_acl` 无人读），Bot 级白名单要新建** |
| 幂等 / 锁 | `app/idempotency.py`、`app/locks.py` | 队列幂等键直接用 |
| 崩溃恢复 | `app/main.py:56` `pipeline.recover_interrupted()` | 升级成通用 lease 回收 |
| 运行事件流日志 | `docs/runs/RUN-*.jsonl` | **skill 录制的原料**，白送 |
| Bot 桥接 + 防死循环 | `app/octo/`（client/inbox/guard/runner） | 消息层基座，扩成 Bot↔Bot 协议 |
| Web 工作台 + Tauri 壳 | `web/`（React18+Vite，devDeps 已含 `@tauri-apps/cli` 2.12） | 手机端直接复用 |
| 定时器 | `app/ui/ops.py`（`scheduled_jobs` + ticker） | 升级成 cron + 补偿 |

### 2.2 七个结构性障碍（这是方案真正要解决的问题）

| # | 障碍 | 证据 | 后果 |
|---|---|---|---|
| B1 | **单进程硬约束** | `deploy/gunicorn.conf.py:27` `workers = 1`，注释列出三条理由 | 无法水平扩展；一个长任务卡住全站；重启即断 |
| B2 | **SQLite 单写者** | `app/db.py` 全局 sqlite3；gunicorn 注释 `database is locked` | 多 worker / 多 Bot 并发写必炸 |
| B3 | **子进程句柄在进程内存里** | `app/engine/dsh_adapter.py:50` `_RUN_PROCESSES` | 停任务必须打回属主进程；重启后失控子进程变孤儿 |
| B4 | **后台线程全靠 lifespan 单例** | `app/main.py:57-82`（watcher / 审批轮询 / cron / octo runner） | 多实例会重复推送、重复跑定时 |
| B5 | **隔离层是 Windows 专用** | `app/engine/lowil.py`、`docs/sandbox.md`（icacls / Low IL / taskkill） | 上 Linux 必须换实现；且现在是**任务级目录**不是 Bot 级常驻 |
| B6 | **没有队列，创建即执行** | `api/tasks.py:176-183` 请求线程同步执行；`db.spawn_bg_thread` 无上限 | 来一个任务起一个线程/子进程，N 个项目 = N 线程 + N 子进程，没有背压 |
| B7 | **重启无孤儿清理 + 两套状态真相** | 全仓无 `running` 复位；`recover_interrupted` 只修 retrying；`_assert_startable` 拒绝 `running` | 重启后残留 run 永远 running、旧子进程继续烧 token、锁 30 分钟才过期 |

> 注意：B1–B4 不是一个补丁能解决的，它们共同指向同一个结论 —— **把有状态的部分搬出 web 进程**。这正是第 3 节架构的由来。

### 2.3 SQLite → Postgres 的真实改造面（已扫过全仓）

好消息：**几乎所有模块只是 `import sqlite3` 拿类型标注，真正的连接只有 `app/db.py` 两处**（`sqlite3.connect` ×2）。所以不需要改 48 个模块的连接逻辑，重点是方言。

| 方言点 | 全仓命中 | Postgres 写法 | 改造方式 |
|---|---|---|---|
| `?` 占位符 | 全局（每个 SQL 都是） | `%s` | **适配层自动翻译**，不改业务 SQL |
| `PRAGMA table_info(x)` | 约 20 处（幂等迁移判列） | `information_schema.columns` | 封装 `db.columns(conn, table)` 统一替换 |
| `INSERT OR IGNORE` | 约 15 处 | `ON CONFLICT DO NOTHING` | 逐个替换（机械） |
| `INSERT OR REPLACE` | 约 8 处 | `ON CONFLICT (id) DO UPDATE SET …` | 逐个替换（要写全字段，注意） |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | 3 处（`finance.py:77`、`ui/store.py:29,102`） | `GENERATED BY DEFAULT AS IDENTITY` | `app/schema.sql` 改 + 迁移脚本 |
| `PRAGMA journal_mode/busy_timeout/foreign_keys` | `db.py:113-147` | 无对应，直接删 | 连接层改造 |
| `datetime('now')` / 字符串时间比较 | 分散（项目统一存 `'YYYY-MM-DD HH:MM:SS'` 文本） | `timestamptz` 或继续存文本 | **建议继续存文本**，零改造；只给新表用 `timestamptz` |

**降低风险的三招**
1. **适配层**：`app/db.py` 里加一个 `Connection` 包装，`.execute()` 自动把 `?` 换成 `%s`、拦截 `PRAGMA` 转 `information_schema`。业务 SQL 一行不改 —— 这一步能把 8–12 天压到 5–7 天。
2. **不动时间格式**：全仓已有"文本时间"约定（`octo/guard.py:35` 等），迁移时保留，避免时间类型转换踩坑。
3. **双跑一周**：`DB_BACKEND=sqlite|postgres` 开关，测试全跑 PG、生产先读 PG 写 SQLite，稳定后切。

> 仓库里已有 `app/schema.sql`（含初始化种子数据），Postgres 版 DDL 直接由它改写，比从代码里反推表结构靠谱得多。

> ⚠️ **迁移不只是方言**：它顺手逼出三个必须修的并发正确性问题 —— ①`settle_budget` 的读改写（`dsh_adapter.py:299-311`）②两套状态真相（2.5 #4）③`locks` 的 1800s TTL 死锁窗口（2.5 #3）。**这三条修完才敢多进程跑**，所以它们要算进 P1，不能留给"以后优化"。

### 2.4 代码考古的 7 个关键事实（会直接改变实现方式）

> 以下全部经逐行核对，不是推测。**其中 3 条推翻了我最初的乐观假设**，已标 ⚠️。

| # | 事实（文件:行） | 对方案的影响 |
|---|---|---|
| ⚠️1 | **Bot 没有可执行的人格和模型**。`octo_bots`（`app/schema.sql:305-315`）只有 token/role/预算；`ai_employees.soul` 全仓**没有执行期引用**；`ai_employees.model` 不传给 run；preset 由 `tasks.owner_employee_id` 反查（`runner_acp.py:154-169`），而 Octo 建的任务不写这个字段 → **恒为 standard 权限** | 4.1 的 `bots` 表必须新增 `persona/model`，并且**要改 runner 把 model/preset/persona 真正传进去**。这是方案里最大的一块"看不见的活"，漏了它做出来还是同一个通用 agent 换个名字 |
| ⚠️2 | **工具级权限是空实现**。`positions.tool_acl / dir_acl`（`schema.sql:33-34`）有字段，但**全仓无任何读写代码** | 4.7 的"每 Bot 工具白名单"是**新建功能**，不是接线。P3 的工期要包含它 |
| ⚠️3 | **记忆没有检索**。`memory.read` 就是 `WHERE scope IN (…) ORDER BY created_at DESC LIMIT 200`（`memory.py:82-94`），无关键词、无相关性 | P4 要新建检索。好消息：`experience.context_for`（`experience.py:486-541`）已经有一套可用打分（标签交集×3.0 + 2-4 字 n-gram×0.8 + 新鲜度 + weight，阈值 2.0）——**直接沿用，别另起炉灶** |
| 4 | 控制台群已有跳数闸：`store.hop_allows` **4 hops / 600s**（`store.py:234-252`） | E2 的"hops≤6"是在现有基础上扩展，不是从零建 |
| 5 | 定时任务名不副实：`scheduled_jobs` 只有 `HH:MM` 一天一次，且 `run_job_now` **忽略 job.prompt**，固定跑内置 `daily_digest`（`ops.py:182-192`）；无重试、无补偿、无告警 | E4 的 scheduling 基本是重写，不是改造 |
| 6 | **Octo 里 Bot 不能派活**：`runner.py:116-122` 的 bot 分支硬编码只回「收到 @…，我按分工继续」，注释写明 `never start a task`；且 Bot API 禁止 bot 拉 bot 进群（`client.py:3-6`） | E2 必须**自建派单信道（走控制面，不走平台群）**，群只当展示层。这条设计定死，否则会被平台 API 卡死 |
| 7 | 企微入口很能打但有硬约束：单发 `touser`、无群语义、**无消息历史表**、只处理 text + template_card_event、无附件/语音、prompt 上限 500 字（`bridge.py:220-221`）、且**不过意图闸**（"你好"也会建任务，对比 `octo/intent.py` 有分类） | E5 要做三件事：**意图分类前置**、**消息持久化**、**附件/语音通道**。不做这三件，手机端体验会很糙 |

### 2.5 执行链路考古的 10 个关键事实

> 这次把"任务从入队到跑完"整条链路读完了。**结论比 2.4 更扎心：现在没有队列，也谈不上"常驻"。**

| # | 事实（文件:行） | 对方案的影响 |
|---|---|---|
| 1 | **没有队列，创建即执行**。`POST /runs/{id}/execute` 在**请求线程里同步**调 `execute_run`（`api/tasks.py:176-183`）；UI 聊天是裸 `threading.Thread`（`ui/routers/chats.py:127-129`）；`db.spawn_bg_thread` **无池、无上限**（`db.py:333-360`） | 4.2 的队列不是"升级"，是**从无到有**。E1 现在只做到"服务器不关就能跑"，且来一个任务起一个线程/子进程 |
| 2 | **重启后没有孤儿清理**：全仓没有代码复位 `task_runs.state in ('running','spawned')`；`recover_interrupted` 只修 `projects.state='retrying'`（`pipeline.py:1819`）；存下的 PID 被明确拒绝使用（`dsh_adapter.py:64-72`） | 重启后残留 run **永久 running**、trace 永久 running、旧子进程变孤儿继续烧 token。reconciler 是 P1 必做项，不是锦上添花 |
| 3 | **崩溃后有 30 分钟死锁窗口**：`run_locks` 只能靠 TTL 过期，`M0_LOCK_TTL` 默认 1800s（`locks.py:22-23`） | 进程一崩，同 task/session 半小时内重试一律 409。租约 TTL 要降到 60–90s + 心跳续约 |
| 4 | **两套状态真相**：`tasks.state`（state_machine 推）与 `task_runs.state`（各处 UPDATE）会不一致；且 `_assert_startable` 不接受 `'running'`（`dsh_adapter.py:487-495`） | 新架构必须收敛：**`jobs` 是唯一真相**，`task_runs` 降为"执行流水"，启动前只信 jobs |
| 5 | **预算读改写没有乐观锁**：`settle_budget` 直接 `used_tokens = used_tokens + ?`（`dsh_adapter.py:299-311`），现在靠 SQLite 单写者兜底 | 上 PG 必须加行锁/原子更新，否则多 worker 丢更新。**这是"迁移不只是改方言"的典型** |
| 6 | **DSH 会话存储全仓共用**：默认 `M0_SANDBOX_ACP=0`（`runner_acp.py:179`），只有 `session/new` 的 cwd 做隔离；同会话双开直接报 `already owned by an active write handle`（`locks.py:1-8`） | 4.1 的"每 Bot 一个 DSH_HOME"是**硬需求**，不是优化。否则多 Bot 并行必然打架 |
| 7 | **生产配置里常驻线程全关**：`UI_CRON / WECOM_WATCHER / WECOM_APPROVAL_POLLER / OCTO_RUNNER` 全是 env 开关，`.env.production.example:71-76` 四个全是 0 | "关掉页面还在干"在现在的生产模板里**根本没开**。P1 第一件事：改成默认开 + 有守护 + 有健康检查 |
| 8 | **Linux 部署其实已有底子**：`deploy/agent-os.service` 已按 Linux 写好（`ReadWritePaths=/opt/agent-os/{data,workspace,docs/runs}`，`M0_SANDBOX=none`），`.env.production.example` 也在 | P0 的 Linux 化比预想轻。真正要改的是**写死路径**：工作区默认 `LOCALAPPDATA`（`config.py:35-38`）、run 日志写进代码仓 `ROOT/docs/runs`（`dsh_adapter.py:178`）、DSH CLI 默认 Windows 路径（`dsh_adapter.py:40-44`） |
| 9 | **产物收集有硬上限**：`SCAN_MAX_ENTRIES=2000 / SCAN_MAX_FILES=50`（`dsh_adapter.py:737-739`） | Bot 家目录长期积累后会被截断。改成"按任务目录收 + 索引表"，别再全盘扫 |
| 10 | **生成代码可直接执行、无隔离**：`pipeline.execute_test` 直接 `subprocess.run([sys.executable, path])`（`pipeline.py:1415,1429`）；`runner_native.run_sandbox_script` 只是 Popen（`483-506`） | Linux 上等于"以服务账号跑模型生成的任意代码"。P3 容器化必须一起解决，否则是最大的安全债 |

**Windows 专有点清点**：共 **18 处**，其中 `M0_SANDBOX=none` + `M0_SANDBOX_ACP=0` 可绕过 3 处，**剩下 15 处必须换实现**（`kill_tree` 的 taskkill、`mount_shared` 的 mklink、`lowil.py` 整个模块、`sandbox_env.py` 的 node 路径白名单、`l2_scripts._clean_env` 的 SYSTEMROOT/USERPROFILE 等，完整清单见 `2.5` 对应的考古记录）。

---

## 3. 目标架构

### 3.1 分层图

```
┌──────────────────────────── 入口层（都可不要，API 是唯一真相） ────────────────────────────┐
│  微信/企微（手机+PC）    飞书    PWA（手机浏览器）    桌面 Tauri 壳    REST/CLI/Webhook        │
└───────────────────────────────────────┬──────────────────────────────────────────────────┘
                                        │ HTTPS / WebSocket / SSE
┌───────────────────────────────────────▼──────────────────────────────────────────────────┐
│  web  ·  无状态 · N 个 worker 可扩                                                        │
│  鉴权 / 会话 / 实时推送 / 审批 UI / 产物预览 / 管理台                                       │
│  写操作只有一种形态：insert 一条 job/outbox 记录，然后立刻返回 202                            │
└───────────────────────────────────────┬──────────────────────────────────────────────────┘
                                        │ Postgres（唯一真相）+ pg_notify（唤醒）
┌───────────────────────────────────────▼──────────────────────────────────────────────────┐
│  kernel  ·  单实例有状态 · 可热备                                                          │
│  ┌────────────┬────────────┬────────────┬────────────┬────────────┐                      │
│  │ 队列调度    │ 租约/心跳   │ 预算闸      │ Bot 生命周期 │ 协作路由    │                      │
│  │ lease 续约  │ 孤儿对账    │ reserve/settle│ 容器启停   │ handoff    │                      │
│  └────────────┴────────────┴────────────┴────────────┴────────────┘                      │
│  对外只暴露：/kernel/enqueue /kernel/cancel /kernel/status                                 │
└───────────┬──────────────────────┬──────────────────────┬────────────────────────────────┘
            │                      │                      │
   ┌────────▼────────┐   ┌─────────▼────────┐   ┌─────────▼────────┐
   │ worker-1        │   │ worker-2         │   │ worker-N         │  无状态，按 CPU/并发扩容
   │ 领 job → 跑 run │   │                  │   │                  │  崩了 lease 自动回收
   └────────┬────────┘   └─────────┬────────┘   └─────────┬────────┘
            │ docker exec / attach │                      │
┌───────────▼──────────────────────▼──────────────────────▼────────────────────────────────┐
│  Bot 容器池  ·  每个 Bot = 一台常驻"电脑"                                                  │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐                        │
│  │ bot:小文          │ │ bot:前端小前      │ │ bot:财务          │                        │
│  │ chromium+xvfb    │ │                  │ │                  │                        │
│  │ CDP :9222        │ │  (冷启动 ≈3s)     │ │  (闲置自动休眠)    │                        │
│  │ python/node/终端  │ │                  │ │                  │                        │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘                        │
│           │  持久卷（按 Bot，不随任务销毁）            │                                  │
│           ▼                    ▼                    ▼                                  │
│   /home/bot/{workspace, chrome-profile, .tmp, skills, memory.db}                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 三条数据流（实现时按这个顺序写）

**派活流**
```
用户消息 → [入口适配器] → web: POST /jobs（幂等键 = 渠道消息 id）
        → Postgres jobs(status=queued) + pg_notify('jobs')
        → web 立即 202 {job_id}   ← 手机端马上看到"已接单"
```

**执行流**
```
kernel 收到 notify → 选可用 worker（或本地起） → worker: UPDATE jobs SET lease_owner,lease_until
                 → 在 Bot 容器里起 run（ACP / native）→ 事件写 job_events（append-only）
                 → 每 5s 心跳续 lease；usage_update 到达 → kernel 预算闸比对
                 → 结束：settle 预算、跑验收判据、写产物、发通知
```

**回传流**
```
job_events → web SSE/WS → PWA 实时进度
          → 出站适配器 → 企微卡片（完成/需签字/失败）+ 审批单
          → 不需要用户在场，服务端不关心客户端是否在线
```

### 3.3 部署拓扑

单机 docker-compose 起步（够用到 ~10 个 Bot）：

```
nginx (443)  ──►  web ×2          (uvicorn, 无状态)
             └─►  kernel ×1       (systemd 守护, 崩溃自动拉起)
                  worker ×2       (可同机可异机)
                  postgres 16     (卷 + 每日 pg_dump)
                  bot-小文 / bot-前端 / ...  (按需启停, 各自持久卷)
                  
旁路：docker-socket-proxy（只给 kernel 用，避免容器逃逸拿宿主 root）
定时：kernel 内置 cron（替代 ui cron），或独立 scheduler 进程
```

**为什么 kernel 允许单实例**：这是对 B1/B3/B4 代价最小的解法 —— 把"有状态的部分"收进一个明确的地方，web 就能多开、worker 就能扩、重启就有地方恢复。等 P3 之后再考虑 kernel 分片（按 bot_id 哈希）或主备。

---

## 4. 核心模块设计

### 4.1 Bot 运行时：每 Bot 一台"电脑"

**目标**：Bot 有身份证、有家、有登录态、有工具，**任务结束家还在**。

> ⚠️ **前置改造（来自 2.4 ⚠️1）**：现在 Bot 的人格和模型**根本没有执行入口** —— `ai_employees.soul` 全仓无人读、`model` 不传 run、preset 恒为 standard。所以 4.1 不只是"加个容器"，还必须把 `persona / model / tools_allow` 接进 runner 的启动参数。**不做这步，容器里跑的还是同一个通用 agent，只是换了个名字和头像。**

**容器规格（单 Bot）**

| 项 | 值 | 说明 |
|---|---|---|
| 基础镜像 | `debian:12-slim` + chromium + xvfb + python3.12 + node20 + git + curl | 约 900MB，构建一次复用 |
| 用户 | `bot`（uid 1000，非 root） | 容器内 root 关闭 |
| 资源 | `--cpus 1.0 --memory 1.5g --pids-limit 512` | 超限直接 OOM 杀掉，比拖垮宿主机好 |
| 网络 | 独立 bridge，出站放行，**不 publish 端口** | 控制面通过 docker exec / unix socket 通信 |
| 卷 | `/home/bot/workspace`、`/home/bot/chrome-profile`、`/home/bot/.tmp` | 按 Bot 持久，跟任务无关 |
| 生命周期 | 活跃即驻留；闲置 `IDLE_STOP=15min` 销毁容器**但保留卷**；派活时冷启 ≈3–5s | 5 个 Bot 闲置只占 ~0MB，活跃约 600MB–1GB/个 |

**目录布局（对应 Grok Bot 的"共享一台电脑"）**

```
/home/bot/
├── workspace/              # Bot 的"桌面"，跨任务保留
│   ├── tasks/<task_id>/    # 单任务工作区（隔离边界，复用现有 workspace/<agent>/<task>）
│   ├── shared/             # 集团共享资源（现有 RES-DEV / RES-FIN，只读挂载）
│   └── inbox/              # 用户投喂的文件
├── chrome-profile/         # ← 关键：持久 user-data-dir，登录态不丢
├── .tmp/ .dsh/             # 临时目录 + agent 家目录（每 Bot 一个，不再每任务重建）
├── skills/                 # 该 Bot 学会的 skill（可执行 + 说明）
└── memory.db               # Bot 私有记忆（可后续换成 Postgres 行）
```

> **与现状的差异**：现在 `docs/sandbox.md` 是 `DSH_HOME=<task>/.dsh` + `workspace/<agent>/<task>`，**任务之间不共享任何东西**。Grok Bot 的体感恰恰来自"共享"：登录状态、下载过的文件、记住的偏好。改法是：Bot 级目录常驻，**只把写权限按任务收窄**（任务 A 不能写任务 B 的子目录），而不是每次重建整个家。

> ⚠️ **而默认配置下更糟**：`M0_SANDBOX_ACP=0` 时（`runner_acp.py:179`）**所有任务和 Bot 共用一个 DSH session store**，唯一隔离是 `session/new` 的 cwd；同一个 session 双开会直接报 `already owned by an active write handle`（2.5 #6）。所以"每 Bot 一个 DSH_HOME"是硬需求，不做多 Bot 并行必打架。

**浏览器持久化（E3 的地基）**
- 用 `chromium --remote-debugging-port=9222 --user-data-dir=/home/bot/chrome-profile` 常驻在容器内，xvfb 提供虚拟显示。
- agent 通过 CDP 操作（现有 `tools/cdp_eval.py` / `cdp_shot.py` 直接改造），工具集：
  `browse_open(url)` / `browse_click(selector|坐标)` / `browse_type` / `browse_screenshot` / `browse_extract(js)` / `browse_wait_for(条件)`
- **登录态安全**：cookie/密码在持久卷里 → 卷加密（宿主机 LUKS 或 per-bot LUKS 文件）+ 出站域名白名单 + 高危动作（转账/删除/发消息）强制审批。
- 遇到验证码/短信/2FA：产出 `needs_human` 事件 → 推企微卡片（附截图）→ 用户点开后**接管同一个浏览器会话**（用 noVNC 或 CDP 投屏），处理完再交还。这是 Grok Bot 的"hand off to human"。

**执行内核放哪？（三选一，推荐 A 短期 / B 长期）**

| 选项 | 做法 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A 复用 DSH ACP | 容器内装 DSH CLI，沿用 `--profile acp` | 零改造，现有 runner 全复用，行为已验证 | Electron 打包体积大、启动慢、Windows 路径写死（`dsh_adapter.py:40`）、版本耦合 | **P1 先走这条** |
| B 自研轻量 loop | 用 `app/llm.py` 自己实现 tool-calling 循环 | 镜像小（~600MB）、启动快、完全可控、可做真正的"调用前"预算闸 | 要写 ~1500 行（流式/取消/工具注册/重试/权限钩子），2 周 | **P3 再切** |
| C 开源 runtime | OpenHands / browser-use / SWE-agent | 见效快 | 外部耦合、升级不可控、权限模型对不上现有 RBAC | 不推荐 |

> ⚠️ **必须先验证的风险**：`dsh_adapter.py:40` 的默认 CLI 路径是 Windows 的 `%LOCALAPPDATA%\...\dsh.cmd`。上 Linux 前要确认 DSH 有 Linux 发行版、或有 `DSH_CLI` 可指向的 Linux 构建。**这是 P0 的第一个 spike，验证不通过就提前切方案 B。**

### 4.2 常驻任务队列（E1 的地基）

**状态机**（复用现有 task 状态语义，加 lease）

```
queued ──► leased ──► running ──► succeeded
  │           │           ├──► failed ──► retrying ──┐
  │           │           ├──► paused(budget) ───────┤（追加预算后 resume）
  │           │           ├──► needs_human ──────────┘（人工处理后 resume）
  │           └──► lease_expired ──► queued（attempt+1）
  └──► cancelled
```

**关键设计点**

1. **租约而非锁**：`lease_owner + lease_until + heartbeat_at`。worker 每 5s 续约；kernel 每 15s 扫一次过期租约 → 回收 → attempt+1 → 重新入队。worker 崩了任务自动回到队列，**这是"重启不丢"的实现方式**。
2. **幂等**：复用 `app/idempotency.py`。`idempotency_key = channel:msg_id`（企微/飞书消息 id）或 `task_id:attempt`。重复投递不重复执行、不重复扣预算。
3. **断点续跑**：两级 —— ①任务级：`job_events` 里最后一个成功步骤作为 checkpoint，重跑从中断阶段开始（复用 `pipeline.recover_interrupted` 思路）；②会话级：ACP 有 `session/resume`（`docs/acp-protocol.md:11`），同 Bot 的会话 id 可复用。注意 `sandbox.md` 提到"每任务一套 DSH_HOME 所以跨任务不能 resume" —— 改成 Bot 级 DSH_HOME 后，**跨任务 resume 反而变得可行**，这是常驻架构的额外红利。
4. **失败策略**：指数退避（30s/2m/10m），最多 3 次；仍失败 → 死信 `dead_letter` + 企微告警（带失败原因和最后 20 行事件），**不允许静默失败**。
5. **并发与优先级**：按 Bot 串行（一个 Bot 一次只干一件事，符合 Grok Bot"每 Bot 一块屏幕"语义）、跨 Bot 并行；优先级 `interactive > routine > batch`；全局并发上限和每 Bot 每日 token 额度双保险。
6. **孤儿对账 reconciler**：每 60s 对账三张表 —— `jobs(leased/running)` vs `worker 心跳` vs `docker ps`。三方不一致就修：worker 死了收租约；容器在跑但没 job 就杀；job 在跑但容器没了就重试。
7. **预算闸接在哪**：`reserve`（入队时按估价预留）→ `settle`（结束时结算）→ 中途 `usage_update` 超 80% 直接 `cancel` 回合。**复用现有 V4.3 引擎，不改语义**；新增只有"队列等待不占预算、执行才扣"。
8. **唯一真相 + 原子结算**：新架构里 `jobs` 是唯一状态源，`task_runs` 降级为执行流水（保留兼容读），启动前判断只看 jobs（现状两套 state 见 2.5 #4）。预算结算改原子 UPDATE（`used_tokens = used_tokens + %s`），不再读改写（`dsh_adapter.py:299-311` 现在的写法靠 SQLite 单写者兜底，上 PG 会丢更新）。

**已有积木（直接复用，别重写）**

| 要的东西 | 现成的 | 差什么 |
|---|---|---|
| worker 骨架 | `db.spawn_bg_thread` + `pin_thread_db`（`db.py:333,40`） | 加池、上限、领任务语义 |
| 租约 | `locks` 带 TTL 的跨进程锁 + 偷锁审计（`locks.py:47-77`） | TTL 从 1800s 降到 60–90s + 续约 |
| 心跳 | `task_runs.hb_seq/last_hb_at` + `do_report_heartbeat`（`verbs.py:164`）+ 每 run 心跳线程 | 只缺"判死 + 接管" |
| 会话级续跑 | `session_id` 持久化 + `AcpClient.resume`（`acp_client.py:169`）+ 三次重试 | 几乎直接可用 |
| 任务级续跑 | `pipeline.resumable_stage_plan:514`、`reuse_completed_task:539` | 与队列 checkpoint 对接 |
| 常驻循环范式 | `octo/runner.py:199-226` 单例 start/stop/status | **现在是全局单例，只支持一个 bot**，要改成每 Bot 一实例 |

**存储选型（必须拍板）**
| 方案 | 做法 | 工期（实测口径，见 2.3） | 长期 | 推荐 |
|---|---|---|---|---|
| A **Postgres 迁移** | sqlite3 → psycopg + 方言适配；`SELECT … FOR UPDATE SKIP LOCKED` 做队列 | **8–12 天**（含适配层则 5–7 天） | ✅ 多实例、可备份、可观测 | ✅ 推荐 |
| B SQLite + 单写内核 | 所有写走 kernel RPC，web 只读；WAL + busy_timeout | 2–3 天 | ❌ 单机天花板，运维难受，未来还要再迁 | 应急可接受 |

> 我的判断：既然后面要做「多 Bot 并行 + 常驻 + 手机实时推送」，SQLite 会在第 2 周就开始疼。**长痛不如短痛，P1 一起迁掉。**

### 4.3 多 Bot 协作协议（E2 的地基）

**消息模型**：不要发明协议，先把"群聊"做对。

```
conversation (群/DM) ──< members (bot|human) ──< messages
message: {id, conv_id, sender, kind, payload, task_id?, hops, created_at}
kind ∈ say | assign | accept | progress | result | escalate | handoff
```

**Bot→Bot 派单（三事件式，天然可审计）**

```
Bot A: assign{to:B, task_spec, budget_cap, deadline, acceptance_ref}   → 创建子 job，parent_job_id=A 的 job
Bot B: accept{job_id}      ← 超时未接 = 拒绝，A 自己想办法或升级给人
Bot B: result{job_id, artifacts, verdict}
```

**防失控四道闸**（复用 `app/octo/guard.py` 的参数，再加三条）
1. **hop 上限**：`traces.hops ≤ 6`，超了强制 escalate 给人（现有 `OCTO_MAX_BOT_CHAIN=3` 升级为全链路）
2. **预算继承**：子任务额度只能从父任务未花余额里划，不能凭空生钱（复用 finance 池语义）
3. **环路检测**：`trace_id + (from,to,task_hash)` 去重，A→B→A 同任务直接拦
4. **速率/静音**：每 Bot 每分钟消息数、每小时子任务数上限，超限静音并告警（现有 `OCTO_MUTE_SECONDS`）

**"首席 Bot"怎么来**：不要新写一个 orchestrator。把现有 PM Agent（`app/octo/agent.py`）升级成 **planner**：输入一句话 → 输出 DAG（复用 `app/flows.py` / `pipeline_llm.py` 的拆解能力）→ 每个节点映射到某个 Bot → 只负责"派单 + 等结果 + 汇总"，不亲自干活。

**群会话与私聊的一致性**：Web/PWA 上的群、企微群、Octo 群**共享同一个 conversation 记录**（用 `channel + external_id` 做映射）。用户在哪个端说话，都进同一个 `messages` 表，Bot 看到的是同一段上下文。这是"一个 Bot 多端可达"的关键。

### 4.4 Skill 录制与重放（E4 的地基，全新模块）

**核心洞察**：你已经有**最好的原料** —— `docs/runs/RUN-*.jsonl` 里是完整的工具调用序列 + 参数 + 结果。Skill 录制本质是「把一次成功 run 的事件流，编译成带参数的模板」。

**录制流程**（用户视角只有一句话："把这套存下来，以后每周一跑"）

```
1. 选定一次 succeeded 的 run（有验收判据通过作为"成功"的证据）
2. 轨迹抽取：从 job_events 过滤 tool_call/tool_call_update → [(tool, args, result), ...]
3. 参数识别（规则 + LLM 二次判断）：
   - 文件路径里带 task_id 的 → 参数化 {artifact_dir}
   - 日期/时间 → {today} / {week_start}
   - URL 域名固定、path 含数字 → {item_id}
   - 用户输入过的常量（"双十一"）→ 提升为参数并给默认值
4. 去环境噪声：去掉截图/等待/重试类调用，合并连续同类操作
5. 生成 skill.yaml（见下）+ 一个自然语言说明供人读
6. 绑定验收判据（复用 acceptance）+ 让用户试跑一次 → 通过才启用
```

**skill.yaml 草案**

```yaml
name: weekly-pipeline-report
version: 3
owner_bot: bot-analyst
description: 从 CRM 拉本周管道数据生成周报
created_from_run: RUN-e16930414d784b5e
params:
  week_start: {type: date, default: "last_monday", desc: "统计周起始日"}
  top_n:      {type: int,  default: 5}
inputs:  [{name: crm_url, type: url, secret: false}]
steps:
  - id: open_crm
    tool: browse_open
    args: {url: "{crm_url}"}
    expect: {selector: "table.pipeline"}
  - id: extract
    tool: browse_extract
    args: {js: "…"}
    save_as: rows
  - id: write
    tool: write
    args: {path: "reports/weekly-{week_start}.md", content: "{rendered}"}
acceptance: [L0:file_exists, L2:row_count>0, L1:human_sign]
on_failure: {retry: 2, backoff: [60, 300], notify: wecom}
replay_mode: goal_driven   # strict | goal_driven
```

**两种重放模式（都要有）**
- `strict`：按记录的步骤逐个执行，每步校验 `expect`，失败即停。适合"流程固定、系统没改版"。
- `goal_driven`：只给目标 + 关键约束，让 agent 自己选路。适合网页改版频繁的场景。**默认 goal_driven，strict 用于回归测试。**

**定时（升级 `scheduled_jobs`）**
| 现在的 | 升级为 |
|---|---|
| 固定 prompt + 每天一次 | cron 表达式（`0 9 * * 1`）+ 时区 |
| 失败无声 | 失败告警 + 连续失败自动停用 |
| 错过不补 | `catchup: latest / all / none`（服务器维护后补偿） |
| 不能传参 | 绑定 skill + 覆盖参数 |
| 无历史 | `skill_runs` 每次执行留痕（耗时/成本/验收/产物） |

### 4.5 记忆：按 Bot 人格累积

**三类记忆分开存**（混在一起就完蛋）

| 类型 | 内容 | 存储 | 注入方式 |
|---|---|---|---|
| 人格/偏好 | 语气、格式偏好、禁忌、"我是小文，负责内容" | `bots.persona` + `memories(kind=persona)` | 每次 system prompt 全量注入（短） |
| 事实 | "客户 A 的对接人是张三"、"品牌主色 #B8860B" | 现有 `memory.py` scope KV | 按需检索注入（关键词/FTS 起步） |
| 经验 | 这个任务上次怎么做的、踩过什么坑 | run 摘要 + skill | 同类任务时检索 top-3 摘要 |

**写入时机**（避免垃圾记忆）
1. 任务成功 + 人工验收后，由 reviewer Bot 生成 3 行摘要（做了什么/关键决定/坑）→ 写 `experience`
2. 用户说"记住…" → 写 `fact`，并在回复里确认"记住了：…"
3. 用户纠正 Bot（"不是这样，应该…"）→ 写 `persona` 偏好 + 一条 `experience`

**清理**：`last_used_at > 180d` 且未被检索过 → 归档；每条记忆带 `source`（哪个 job/谁说），可追溯、可一键删除（合规）。

**不要做的事**：不要一上来就上向量库。5 万条以内 Postgres `tsvector` + 中文分词够用；真不够再上 pgvector（同库，零运维成本）。

**别重复造轮子**：现有 `memory.py` 的读取是"按 scope 时间倒序 LIMIT 200"，没有相关性；但 `experience.context_for`（`app/experience.py:486-541`）已经实现了一套可用的相关度打分（标签交集×3.0 + 2-4 字 n-gram×0.8 + 新鲜度 + weight，阈值 2.0）。P4 直接把它抽象成通用检索函数，三类记忆共用。

### 4.6 手机端：五种方案，推荐"企微 + PWA 先行"

| 方案 | 开发量 | 推送 | 体验上限 | 成本 | 评价 |
|---|---|---|---|---|---|
| **企微应用/机器人**（已有） | 2–3 天（卡片打磨） | ✅ 原生 | 中（卡片布局受限） | 0 | ✅ **P1 主力**，你已经跑通审批 |
| **PWA**（现有 React 直接加） | 1–2 天 | ⚠️ iOS 16.4+ 需"添加到主屏" | 高（完整工作台） | 0 | ✅ **P1 必做**，几乎白送 |
| **Tauri 2 移动端**（devDeps 已有） | 5–8 天 | ✅ FCM/APNs | 高 | 签名 + iOS $99/年 | 🔶 P2 想做"自己的 App"就走这条，复用现有 React |
| **自研 Flutter/RN** | 1–2 周 | ✅ | 最高 | 同 + 双端维护 | ❌ 除非要语音/相机/后台定位等原生能力 |
| **Telegram Bot** | 1 天 | ✅ 免费 | 中 | 0 | ❌ 国内网络不可靠，但**海外/自用备用可选**（Bot API 极其省事） |
| **飞书** | 2–3 天 | ✅ | 中高（卡片比企微强） | 0 | 🔶 如果你用飞书，体验比企微好 |

**推荐路径**：`P1 = 企微（推送/审批）+ PWA（看细节/传文件）` → 观察两周 → 如果觉得"进企微翻卡片太烦"，再用 **Tauri 2 打包现有 React 成 App**（不是从零写）。

**语音派活（Grok Bot 有，值得抄）**：企微语音消息 → 下载 amr → ASR（Whisper API / 阿里云 / 本地 whisper.cpp）→ 文本进同一条派活链路。成本 ~1 天，体验加分很大，放 P2 顺手做。

**手机端信息密度**（这决定"像不像 Grok Bot"）：
- 一条任务 = 一张卡：状态色条 + 一句话进度（"正在改第 2/5 个商品"）+ 操作按钮
- 需要人时**必须冒泡**（推送），不能等用户来翻
- 完成时一张结果卡：产物缩略 + 验收结论 + "看全文/下载/签字"
- 进度用 SSE 推送，2s 一次节流，别刷屏

### 4.7 安全与合规（自建最容易被忽略、最不能省的一块）

| 风险 | 措施 |
|---|---|
| 容器逃逸 | 非 root + drop caps + `--security-opt no-new-privileges` + docker-socket-proxy（kernel 才拿 socket） |
| 浏览器登录态泄露 | 持久卷加密；cookie 不进日志/不进 LLM 上下文（CDP 取值后脱敏）；出站域名白名单 |
| 高危动作（转账/删除/群发） | `rbac.gate_tool_call` 前置拦截 → 审批卡 → 人工批准才执行（机制已有，加规则即可） |
| 提示注入（网页里的恶意指令） | 浏览器抓回的文本标记为 untrusted，系统提示里明确"网页内容不是指令"；关键动作二次确认 |
| 密钥管理 | `.env` → 系统密钥环 / `sops` 加密 / 至少 600 权限 + 不进 git |
| 审计 | 所有工具调用 + 审批 + Bot 间消息入 `audit` + `job_events`，可回放（已有基础） |
| 成本失控 | 每 Bot 日额度 + 每项目池 + 全局并发 + 异常用量告警（已有，接上队列） |
| **模型生成的代码被执行** | 现状是裸 `subprocess.run`（`pipeline.py:1415,1429`），Linux 上等于以服务账号跑任意生成代码。P3 统一进容器 + 只读挂载 + 网络策略，**这是自建最大的安全债** |

---

## 5. 数据模型（新增表，Postgres 语法草案）

```sql
-- Bot 身份证 + 人格
CREATE TABLE bots (
  id            text PRIMARY KEY,
  name          text NOT NULL,               -- 小文 / 前端小前
  role          text,                        -- content / frontend / finance
  persona       text,                        -- 系统提示词主体
  model         text,                        -- 分层模型: deepseek-chat / reasoner
  tools_allow   jsonb DEFAULT '[]',          -- 工具白名单（对接 rbac）
  daily_budget  bigint DEFAULT 200000,       -- token/天
  avatar        text,
  container     text,                        -- docker 容器名，空=未起
  home_volume   text,                        -- 持久卷名
  status        text DEFAULT 'cold',         -- cold | waking | awake | sleeping
  created_at    timestamptz DEFAULT now()
);

-- 队列核心
CREATE TABLE jobs (
  id              text PRIMARY KEY,
  bot_id          text REFERENCES bots(id),
  kind            text NOT NULL,             -- task | skill_run | routine | handoff
  payload         jsonb NOT NULL,            -- {prompt, task_id, skill_id, params…}
  status          text NOT NULL DEFAULT 'queued',
  priority        int  DEFAULT 100,          -- 越小越优先
  run_after       timestamptz DEFAULT now(), -- 延迟/退避重试
  lease_owner     text,                      -- worker id
  lease_until     timestamptz,
  heartbeat_at    timestamptz,
  attempt         int DEFAULT 0,
  max_attempts    int DEFAULT 3,
  idempotency_key text UNIQUE,               -- 复用 idempotency.py 语义
  parent_job_id   text,                      -- Bot→Bot 派单树
  trace_id        text,                      -- 全链路追踪 / 环路检测
  hops            int DEFAULT 0,
  budget_reserved bigint DEFAULT 0,
  error           text,
  created_at      timestamptz DEFAULT now(),
  started_at      timestamptz,
  finished_at     timestamptz
);
CREATE INDEX ON jobs (status, run_after, priority);
CREATE INDEX ON jobs (bot_id, status);

-- append-only 事件流：UI 进度、审计、skill 录制的原料
CREATE TABLE job_events (
  job_id      text NOT NULL,
  seq         bigint NOT NULL,
  type        text NOT NULL,   -- queued|started|thought|tool_call|usage|artifact|needs_human|done|failed
  payload     jsonb,
  created_at  timestamptz DEFAULT now(),
  PRIMARY KEY (job_id, seq)
);

-- Bot 间消息 / 群会话
CREATE TABLE conversations (
  id text PRIMARY KEY, title text,
  channel text, external_id text,            -- wecom|octo|web + 外部 id（多端同一会话）
  created_at timestamptz DEFAULT now()
);
CREATE TABLE conversation_members (
  conv_id text, member text, kind text,      -- kind: bot | human
  PRIMARY KEY (conv_id, member)
);
CREATE TABLE messages (
  id text PRIMARY KEY, conv_id text, sender text, kind text,
  task_id text, payload jsonb, hops int DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

-- 学到的技能
CREATE TABLE skills (
  id text PRIMARY KEY, name text, owner_bot text,
  version int DEFAULT 1, spec jsonb NOT NULL,         -- skill.yaml 结构化
  source_run_id text, params_schema jsonb,
  acceptance jsonb, replay_mode text DEFAULT 'goal_driven',
  enabled bool DEFAULT false, created_at timestamptz DEFAULT now()
);
CREATE TABLE skill_runs (
  id text PRIMARY KEY, skill_id text, version int, job_id text,
  verdict text, cost_tokens bigint, duration_ms int,
  triggered_by text,                                  -- manual | cron | bot
  created_at timestamptz DEFAULT now()
);

-- 定时（升级现有 scheduled_jobs）
CREATE TABLE schedules (
  id text PRIMARY KEY, bot_id text, skill_id text,
  cron text, tz text DEFAULT 'Asia/Shanghai',
  params jsonb, catchup text DEFAULT 'latest',        -- latest|all|none
  next_run_at timestamptz, last_run_at timestamptz,
  consecutive_failures int DEFAULT 0,
  enabled bool DEFAULT true
);

-- 记忆（与现有 memory.py 并存：这个是 Bot 私有，那个是业务范围）
CREATE TABLE memories (
  id bigserial PRIMARY KEY, bot_id text, kind text,   -- persona|fact|experience
  content text NOT NULL, source text,                 -- job:<id> | human:<uid>
  weight real DEFAULT 1.0, last_used_at timestamptz,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX ON memories USING gin (tsv);

-- 容器/worker 对账
CREATE TABLE workers (
  id text PRIMARY KEY, kind text,                     -- kernel | worker
  last_seen timestamptz, meta jsonb
);
```

**迁移策略**：现有 SQLite 表（tasks / task_runs / project_stages / budgets / artifacts / audits…）用一次性脚本迁到 Postgres，**表名和字段名保持不变**，只改 `app/db.py` 的连接层 + 方言。这样业务代码（`ui/store.py`、`finance.py` 等）改动最小。

---

## 6. 接口契约（新增，尽量少）

**HTTP（web 暴露给入口层/前端）**

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/jobs` | 派活 `{bot_id, prompt, channel, external_msg_id, priority?}` → `202 {job_id}` |
| GET | `/v1/jobs/{id}` | 状态 + 摘要 + 预算消耗 |
| GET | `/v1/jobs/{id}/events` | **SSE** 实时进度（回放 + 增量） |
| POST | `/v1/jobs/{id}/cancel` | 取消（幂等） |
| POST | `/v1/jobs/{id}/resume` | 人工处理完后续跑（复用现有 retry 语义） |
| GET/POST | `/v1/bots` | 列 Bot / 建 Bot |
| POST | `/v1/bots/{id}/wake` | 手动唤醒容器 |
| GET | `/v1/conversations/{id}/messages` | 群历史 |
| POST | `/v1/handoffs` | Bot→Bot 派单（内部，kernel 校验 hop/预算/环路） |
| POST | `/v1/skills/record` | `{run_id}` → 草稿 skill，返回需人工确认的 diff |
| POST | `/v1/skills/{id}/replay` | `{params}` 手动试跑 |
| POST | `/v1/schedules` | cron + skill + 参数 |
| POST | `/webhooks/{wecom,feishu,telegram}` | 入站消息（验签，复用现有 wecom.crypto） |
| WS | `/ws/conversations/{id}` | 群实时消息 |
| GET | `/healthz` `/metrics` | 健康 + Prometheus（token/队列/容器） |

**内部（kernel ↔ worker）**：保持粗粒度，worker 只调 kernel 的 4 个端点
`POST /kernel/lease` `POST /kernel/heartbeat` `POST /kernel/events` `POST /kernel/finish`

**事件类型（写进 job_events，前端/审计/录制共用一套）**
`queued started thought tool_call tool_result usage artifact needs_human budget_pause accepted handoff done failed cancelled`

---

## 7. 分期计划

> 工期按「1 人全职」估；有 AI 辅助编码可压缩 30–50%，但联调/验收不会更快。
> 每期都有**独立可交付、可回滚**，任何时候停下都不算白干。

### P0 · 地基验证（2–3 天）
| 事项 | 产出 | 验收 |
|---|---|---|
| Linux 上跑通现有系统 | **`deploy/agent-os.service` + `.env.production.example` 已有底子**，补齐写死路径后直接起 | 8077 可访问，能跑一条 mock 任务 |
| **DSH CLI Linux 可用性 spike** | 结论：可用 / 不可用 | 不可用就通知切方案 B（自研 loop） |
| Postgres 迁移可行性 | 方言差异清单 + 一条链路跑通 | `tasks` 增删查跑在 PG 上 |
| Bot 容器镜像 v0 | Dockerfile + 起 chromium + 截图 | `docker run` 后 CDP 能截一张图 |

**回滚点**：P0 全程不动主分支；失败就回 Windows + SQLite，损失 3 天。

### P1 · 常驻（1–1.5 周）—— 交付 E1
- Postgres 迁移完成（表结构不变）
- web / kernel / worker 三进程拆分（compose）
- jobs 队列 + lease + 心跳 + 重试 + 死信
- reconciler 对账（worker 死/容器死/job 卡死）
- 企微完成/失败/需签字三类卡片推送（无需客户端在线）
- Bot 级持久家目录（**先不上容器**，用宿主机目录 + systemd-run 隔离，快速见效）
- **Bot 身份证接进执行链路**：`persona / model / preset` 真正传给 run（现在三项全是空转，+1–2 天，见 2.4 ⚠️1）
- PWA 基础版（现有 React 加 manifest + service worker）

**验收**：手机派活 → `restart kernel` → 任务自动续跑 → 企微收到结果卡 → 手机能签字。

### P2 · 团队（1–1.5 周）—— 交付 E2
- conversations / messages 表 + 多端会话统一（企微群 ↔ Web 群同一 id）
- Bot→Bot assign/accept/result + 四道闸（在现有 4 hops 闸上扩展）
- PM Agent 升级为 planner（输出 DAG，节点映射到 Bot）
- 群聊 UI（Web/PWA）+ 各自进度分屏
- 企微侧补三件：**意图闸前置**（"你好"不建任务）、**消息持久化**、**附件/语音通道**（见 2.4 第 7 条）
- 语音派活（ASR）

**验收**：一句话触发 3 Bot 协作，DAG 图可回放，hops 超限自动升级给人。

### P3 · 电脑（1–1.5 周）—— 交付 E3
- Bot 容器化（镜像/资源限额/网络策略/卷）
- 持久 Chrome + CDP 工具集接进 agent loop
- 休眠/唤醒（闲置 15min 停容器，派活冷启 3–5s）
- needs_human 接管（投屏 + 人工处理 + 交还）
- 出站白名单 + 高危动作审批规则

**验收**：跨任务保持登录态；验证码场景正确 escalate；容器 OOM 后任务自动重试。

### P4 · 学习（1–1.5 周）—— 交付 E4
- 轨迹抽取 → skill.yaml 生成 → 人工确认 → 试跑 → 启用
- strict / goal_driven 双模式重放
- schedules 升级（cron + catchup + 连续失败停用）
- memory 三类 + 检索 + 清理

**验收**：手把手教一次 → 存成每周一的 routine → 改参数能重跑 → 错过时间点能补偿。

### P5 · 打磨（3–5 天，可与 P3/P4 并行）
- 监控面板（队列深度/容器数/token/失败率）
- 备份恢复演练（PG + 卷）
- 文档 + 运行手册 + 灾难恢复 SOP
- （可选）Tauri 2 打包成 App

**合计：约 6–9 周（1 人全职）；其中 P1 结束就有"能用的常驻团队"。**

---

## 8. 成本模型

### 8.1 固定成本

| 项 | 规格 | 月成本 |
|---|---|---|
| Linux 服务器 | 8C16G / 200G SSD（国内云） | ¥400–700 |
| 或自有机器 | 旧 PC / 迷你主机 电费 | ¥30–60 |
| 备份存储 | PG dump + 卷快照 | ¥20–50 |
| 域名/证书（可选） | 反代自用 | ¥0–10 |
| iOS 自研 App（可选，P5） | Apple 开发者 | ¥99/年 ≈ ¥60/月 |
| **合计** | | **¥450–800/月** |

### 8.2 Token 成本（按 DeepSeek 官方 2026-10 价目实算）

单位：元 / 百万 tokens（[官方价目页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)）

| 模型 | 输入·缓存命中 | 输入·未命中 | 输出 | 上下文 |
|---|---|---|---|---|
| `deepseek-flash` | 0.02–0.04 | 1–2 | 4–8 | 1M |
| `deepseek-v4-pro` | 0.15–0.30 | 4.5–9 | 13.5–27 | 1M |

> 高峰时段 = 工作日 9:00–12:00 / 14:00–18:00；**其余时段（含周末、法定假日）全部半价**。
> 这对"定时任务放晚上跑"是天然红利。

**单任务实算**（flash、输入 60k / 输出 5k、缓存命中 60%、空闲时段）

```
未命中输入 24k × ¥1/M    = ¥0.024
命中输入   36k × ¥0.02/M = ¥0.001
输出        5k × ¥4/M    = ¥0.020
                         ≈ ¥0.045 / 任务（高峰时段约 ¥0.09）
```

| 用量档 | 日任务 | 日成本 | 月成本 |
|---|---|---|---|
| 轻度（自己偶尔用） | 5 | ¥0.25–0.5 | **¥8–15** |
| 中度（当工作助手） | 20 | ¥1–2 | **¥30–60** |
| 重度（routine + 长报告，200k/任务） | 20 | ¥3–6 | **¥90–180** |
| 规划层换 v4-pro（约占 10% token、单价约 4–5 倍） | — | ×1.5 | 约 +50% |

**别忽略的零头**
- `Search/Fetch` 自 2026-11-01 起按次收费（¥5 / 1000 次）。一个调研任务抓 10–30 页 ≈ ¥0.05–0.15，基本可忽略。
- 浏览器任务的截图会走视觉 token，同类任务成本抬高 20–40%。
- 并发 Bot 越多，"每 Bot 都要带一份上下文"的固定开销越明显 → 这是分层模型 + 记忆压缩最值钱的地方。

> 以上是量级估算，不是报价。**真实账单永远以平台的用量页为准**，上线第一周务必每天对账。

### 8.3 对标

| | 月成本 | 额度 | 数据 | 定制 |
|---|---|---|---|---|
| Cursor Ultra（含 Grok Bot 入口） | ≈ ¥1450（$200） | 有周额度上限 | 在别人云上 | 不能改 |
| **自建（本方案）** | **¥450–1000** | 只受你的钱包限制 | **全在你服务器** | 完全可改 |

> **结论修正**：不只是"不省钱但换来自主权"—— 按现价算，**中度使用下自建确实更便宜**（¥500–800 vs ¥1450），重度使用也基本打平。
> 省钱的三个杠杆：①定时任务排到空闲时段（半价）②上下文复用打满缓存命中（差 50 倍）③规划用 v4-pro、执行/总结用 flash。

---

## 9. 风险与预案（Top 10）

| # | 风险 | 概率 | 影响 | 预案 |
|---|---|---|---|---|
| R1 | **DSH CLI 无 Linux 版** | 中 | 高（P0 卡死） | P0 第一件事验证；不可用就切自研轻 loop（方案 B），工期 +5 天但架构更干净 |
| R2 | SQLite→PG 迁移踩坑（方言/事务/并发语义） | 高 | 中 | 表名不变、只改连接层；先迁只读链路再迁写链路；保留双跑开关一周 |
| R3 | 容器里跑 Chromium 不稳（画面黑、OOM、CDP 断连） | 中高 | 中 | 用 `headless=new` 不用 xvfb 兜底；健康探针 + 自动重启容器；截图失败重试 2 次 |
| R4 | 多 Bot 并发把 token 烧穿 | 中 | 高 | 每 Bot 日额度 + 全局并发上限 + 异常告警（预算引擎已有，接上即可） |
| R5 | 持久登录态被盗/滥用 | 低 | 极高 | 卷加密 + 出站白名单 + 高危审批 + 定期轮换 + 审计告警 |
| R6 | 提示注入导致它乱点乱发 | 中 | 高 | 网页内容标记 untrusted；渠道发送类动作强制人工确认 |
| R7 | 范围蔓延（想做成完整 Grok Bot） | 高 | 中 | 守第 10 节"不做清单"，每期只交付一条 E |
| R8 | 单机内核成为新单点 | 中 | 中 | kernel 崩溃由 systemd 拉起 + 租约自愈（15s）；P6 再考虑主备 |
| R9 | **重启后孤儿进程继续烧 token** | 高（现状必现） | 中 | reconciler 三方对账（job / 心跳 / 进程）+ 启动时全量复位；租约 TTL 降到 60–90s |
| R10 | 迁移时只改方言、没修并发语义 | 中高 | 高 | 把 2.3 里那三条（预算读改写/双状态/锁 TTL）列为 P1 的**验收项**，不是优化项 |

---

## 10. 明确不做（防止把自己坑进去）

1. **实时语音通话**（Grok Bot 那种"打电话给 Bot"）—— 需要 WebRTC 全套，收益低，至少 P6 后再说
2. **多租户计费/给外人用** —— 自用不需要，现有 tenancy 保留但不扩
3. **桌面端全自动 computer-use**（任意坐标点击、拖拽、多显示器）—— 浏览器内自动化就够 90% 场景
4. **从零写原生移动 App** —— 用 PWA/Tauri 包壳，除非出现必须原生的功能
5. **复杂的向量记忆/RAG 平台** —— Postgres FTS 起步，真不够再上 pgvector
6. **Kubernetes** —— 单机 compose 到 10 个 Bot 都够用，上 k8s 是给自己找活

---

## 11. 待你拍板的 6 个决策

| # | 决策 | 选项 | 我的推荐 | 影响 |
|---|---|---|---|---|
| **D1** | 存储 | A. Postgres 迁移 / B. SQLite+单写内核 | **A** | 决定后面所有并发能力，晚改成本翻倍 |
| **D2** | 执行内核 | A. 复用 DSH ACP / B. 自研轻 loop | **P1 用 A，P3 评估 B** | 先看 P0 的 Linux 兼容性结论 |
| **D3** | 部署形态 | A. docker-compose 单机 / B. k8s | **A** | B 会多 1–2 周且没有收益 |
| **D4** | 手机端 | A. 企微+PWA / B. 直接 Tauri 2 / C. 自研原生 | **A 先行，两周后看要不要 B** | B/C 会推迟 P1 交付 |
| **D5** | 用户数 | A. 只有你 / B. 团队 3–5 人 | **A 起步** | B 要补登录/隔离/配额，+3–5 天 |
| **D6** | 成本上限 | 每日 token 硬顶 + 并发 Bot 数 | 建议 **200k tokens/天/Bot、并行 3 个** | 防止试跑阶段烧钱 |

**还有两个可以顺手定的小事**：
- 语音派活要不要 P2 就做（+1 天，体验加分明显）→ 建议要
- 备份策略：每日 PG dump + 每周卷快照，保留 14 天 → 建议采纳

---

## 12. 一句话总结

**这不是"再做一个 Grok Bot"的工程量，是把你已有的 AgentOS 从"单机任务系统"升级成"常驻多 Bot 平台"。**
最关键的三个动作：**换存储（PG）→ 拆进程（web/kernel/worker）→ 给 Bot 一个家（持久卷 + 持久浏览器 + 容器）**。
做到 P1 你就有了"关掉页面它还在干"；做到 P4 你就有了一个真的 AI 团队；P0 的第一天就能知道 DSH 在 Linux 上能不能用 —— 那是唯一可能推翻整套方案的不确定点。

---

## 附录 A · 对照 Grok Bot：抄什么、不抄什么

| Grok Bot 的设计 | 我们抄不抄 | 我们的实现 | 理由 |
|---|---|---|---|
| 每个 Bot 一台常驻云电脑 | ✅ 抄 | Docker 容器 + 持久卷 + 持久 Chrome | 这是"体感像同事"的来源 |
| 同一账号的 Bot 共享一台电脑 | ⚠️ 部分抄 | 共享 `shared/` 只读资源；可写区按 Bot 隔离 | 完全共享会让 A 的脏数据污染 B；共享只读资源已够用 |
| 关掉客户端任务继续跑 | ✅ 抄 | 服务端队列 + 租约 + 推送 | 这是最值钱的一条 |
| Bot 互相派单、群里协作 | ✅ 抄 | assign/accept/result + hop/预算/环路四道闸 | 我们比它多一层预算控制 |
| 示范一次 → 存成 skill → 定时 | ✅ 抄 | 从 run 事件流编译 skill.yaml | 我们已有 jsonl 轨迹，天然优势 |
| 遇到要人时交还控制权 | ✅ 抄 | needs_human 事件 + 接管投屏 + 审批卡 | 现有企微审批直接复用 |
| 上下文累积（记忆） | ✅ 抄（分层） | persona / fact / experience 三类分开 | 单一记忆库必然膨胀失效 |
| 实时语音对话 | ❌ 不抄 | 只做"语音消息→ASR→文本" | WebRTC 全套成本高、收益低 |
| 全自动 computer-use（任意坐标拖拽） | ❌ 暂不抄 | 只做浏览器内 CDP 自动化 | 覆盖 90% 场景，风险低得多 |
| 自带桌面/移动原生 App | ❌ 暂不抄 | 企微 + PWA，必要时 Tauri 2 包壳 | 省 2–4 周，体验损失小 |

## 附录 B · 参考资料

- Grok Bot 官方文档：<https://docs.x.ai/grok-bot/overview>（产品形态、共享电脑模型、平台与套餐）
- 中文报道（背景：xAI 收购 Cursor 后的首个公开 agent 产品）：<https://www.163.com/dy/article/L458EDO90514R9OJ.html>
- DeepSeek 价目（本方案成本模型依据）：<https://api-docs.deepseek.com/zh-cn/quick_start/pricing/>
- 仓库内既有设计文档：`docs/sandbox.md`（隔离）、`docs/acp-protocol.md`（执行协议）、`docs/octo_bridge.md`（Bot 桥接）、`docs/V4.3.0_发版说明.md`（预算引擎）

## 附录 C · 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| V0.1 | 2026-10-26 | 首版：现状盘点、目标架构、五模块设计、数据模型、工期成本、风险与决策清单 |
| V0.2 | 2026-10-26 | 补入逐行代码考古结论（2.4，7 条）：Bot 人格/模型无执行入口、工具 ACL 空实现、记忆无检索、定时任务名不副实、平台侧 Bot 不能派活、企微入口的 3 个硬缺口；PG 迁移工期按实测方言点修正为 8–12 天；成本模型改用 DeepSeek 官方现价实算 |
| V0.3 | 2026-10-26 | 补入执行链路考古（2.5，10 条）：**没有队列、创建即执行**、重启不清理孤儿、30 分钟锁死窗口、两套状态真相、预算读改写无锁、DSH session 全仓共用、生产模板常驻线程全关、18 处 Windows 专有点清单、生成代码裸执行；P0 改为复用已有 Linux service；新增"已有积木"复用表和 R9/R10 风险 |

