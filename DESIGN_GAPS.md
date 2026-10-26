# DESIGN_GAPS

设计稿（M0 开发包 v2）没写细、或实测才发现的地方。规则：**先记录，不发明**；代码里凡是绕过的地方都指向这里。

## 已解决（实测有结论，记下来防再踩）

1. **dsh CLI 入口不在 PATH** — 实际位置
   `%LOCALAPPDATA%\Programs\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd`，版本 `0.2.1-rc.2`。
   `which dsh` 为空，但 PATH 里那条 `resources\runtime\cli\bin` 目录下只有 `dsh.cmd`（bash 的 which 不匹配 .cmd）。
   适配层默认走这个绝对路径，可用 `DSH_CLI` 覆盖。

2. **Windows 下任务不能作为位置参数传给 headless** —
   `dsh --profile headless --json "任意带空格的指令"` 会失败，stderr 是
   `'C:\Users\...\Programs\DeepSeek' 不是内部或外部命令`（启动器 `%*` 在空格处拆开了 app 路径）。
   可用写法：`printf '指令' | dsh --profile headless --json -`（stdin + `-`）。适配层只用这一种。

3. **headless 不读桌面端凭据库** — 直接跑会报
   `MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"`；
   必须在启动环境里导出 `DEEPSEEK_API_KEY`（`.env` 只给外壳用，不参与 DSH 鉴权）。

4. **事件流真实 schema 已抓到**（见 `docs/dsh-events.md`），字段名以实测为准：
   `session / status / thinking / text / tool_call / tool_result / final`。
   `step_end.usage.totalTokens` 是唯一可用的 token 数；`session.sessionId` 可直接给 `--session-id`。

## 未解决（M0 范围内接受，M0.5 再定）

5. **预算熔断：ACP 已做到"下一次调用前"，headless 仍只有整笔预扣。**
   ACP 每条 `usage_update` 到达时那一次调用已经发生，所以闸门是"越线立刻 `session/cancel`"，
   实测 cap=2000 在 9264 处被取消（超支 7264 = 一次调用的成本），本轮后续调用不再发生。
   headless 连这个都做不到，只能在开跑前整笔预扣、跑完结算。零超支见 §13。

6. **`usage` 的口径** — ACP 已确认是"按会话累计"（同一会话第二轮从 12112 起跳到 13077），
   外壳按 `baseline` 结算 delta；headless 的 `step_end.usage.totalTokens` 尚无跨会话样本，
   目前套用同一套 baseline 减法，等有 resume 样本再校准。

7. **L2 的"沙箱"只是进程隔离** — 独立进程 + 30s 超时 + 剥离代理环境变量，**不是** OS 级 jail。
   M0 的脚本只读产出文件、不联网；真要隔离得上容器或 Windows Job Object。

8. **headless 的权限策略对哪些工具有效未测** — `write` 实测直接执行（无审批），
   `bash` / 网络 / 越权路径未测。M0 只依赖 `write`，其余不假设。

9. **`supervisor_check` 这个 check_type 没有实现** — 包里出现过但 M0 没有 supervisor 角色，
   代码里未支持，出现即返回 400 `UnsupportedCheckType`。

10. **状态机拒绝时的审计只在应用侧** — SQLite 触发器能拦住任何连接的直接 UPDATE，
    但"被拒绝"的审计行只有应用层捕获异常时才写；纯外部 SQL 直连只有 ABORT，没有审计。
    这是 SQLite 的能力边界，不是遗漏。

11. **`--session-id` 续跑未接入** — 字段已存进 `task_runs.session_id`，但 M0 没有"续跑"入口；
    打回重跑走的是新 run（新 run_seq），不是 resume 同一 session。

12. **本机代理会吃掉 localhost 请求** — `curl localhost:8077/...` 走 7890 代理，返回 502 / 空 body；
    `httpx` 默认 `trust_env=True` 同样中招。所有本地调用必须 `curl --noproxy '*'` 或
    `httpx.Client(trust_env=False)`。这是环境事实，不该在代码里硬编码绕过。

13. **零超支熔断需要 DSH 插件，ACP 到不了。**
    DSH 内部每次模型请求前会跑 `agent/request` waterfall（文档原文：
    "`step/start` precedes the `agent/request` waterfall and `prepareCall()`"），
    插件可以在这里直接拒绝，调用根本不会发出。ACP 客户端只能"事后取消"。
    要不要为这 1 次调用的超支写一个 Cordis 插件，是 M0.5 的取舍，不是能力缺失。

14. **ACP 的这个构建没有逐工具审批。** 写文件的 `tool_call` 直接执行，客户端收不到
    `session/request_permission`；所以"工具级"闸门目前不存在，只有预算闸。
    若将来要拦危险工具，同样得走插件层（`tool_call` waterfall / 权限策略）。

15. **会话写句柄要靠 `session/close` 释放。** 只 kill 进程不够：
    `dsh.cmd` 是批处理壳，杀它不会带走 electron 子进程，下一个进程 resume 时报
    `already owned by an active write handle`。收尾顺序固定为
    `session/close` → 关 stdin → `taskkill /F /T`。

## M0.5 阶段新增

16. **决策过滤的门槛是种子值，不是公司制度。** FR-01~FR-99 的 5 千 / 5 万 / 2 万 / 合规全升
    都是占位参数，必须换成真实的分授权体系（谁有权批到多少、哪些事项法务必须签）。
    改法：只改 `decision_filter_rules` 表，代码不用动。

17. **`decided_by` 是调用方自报，没有身份认证。** 现在谁调 `/decisions/{id}/decide` 都能签
    "ceo"。M0.5 只管流程与留痕，不管"你是不是 CEO"；这属于权限体系，M1 再做。

18. **任务目录隔离已实现（Low 完整性级别），仍有三个边界。**
    已做：`workspace/<task_id>/` + `icacls ... (OI)(CI)L` + launcher 把自身令牌降到 Low，
    任务外写入被内核拒绝；`DSH_HOME`/`TEMP` 一起搬进任务目录。实测
    `PermissionError: [Errno 13] Permission denied: '..\outside.txt'`，父目录无文件。
    未做：
    a) **读没限制** —— Low 进程仍可读全盘（"只能读不能写"是当前语义，不是"看不见"）；
       要做到不可见得上 AppContainer，按包 SID 授权。
    b) **打标签需要 WRITE_OWNER** —— UAC 过滤令牌下 icacls 直接 `拒绝访问`，
       launcher 退化成"只提权这一条 icacls"；UAC 静默的机器无感，别处会弹一次。
    c) **只约束文件写入**，网络、CPU/内存、子进程数量没管（要 Job Object 补）。

19. **决策包没有和任务/验收链路强绑定。** `decision_packages.task_id` 是可选外键，
    现在允许独立提案；什么情况下必须由任务自动生成决策包（比如 L1 判据连续两次不通过），
    属于 M1 的编排规则，未定义。

## M1 阶段新增

20. **身份密钥明文存在 SQLite 里。** HMAC 校验需要密钥本身，哈希不行，所以 `identities.secret`
    是明文。DB 因此进入信任边界；要更进一步得用 `.env` 里的 KEK 做 AES-GCM 加密，未做。

21. **没有密钥轮换与过期。** 身份只有 active/revoked 两态，签发后永不过期；
    轮换 = 重新签发 + 手工换 `.env`，没有平滑过渡（双密钥并存）机制。

22. **工具策略是"尽力拦截 + 沙箱兜底"，不是硬保证。** `tool_call` 通知到达后我们取消会话，
    但取消与工具执行有竞态：慢一步时工具已经开始跑。此时唯一的硬边界是 Low 完整性沙箱
    （写目录外必然失败）。要硬的就得在 DSH 插件层拦，已放弃（见 §13）。

23. **签名只覆盖 HTTP 请求，不覆盖内部调用。** 适配层直接调 `verbs.do_*`（进程内），
    不经过签名；也就是说"外壳内部"是可信的，只有外部客户端要验签。

24. **没有限流/配额。** 单个身份可以无限次调用（预算限制的是 token 与金额，不是请求数）。

## M2 阶段新增

25. **隔离在应用层，不在数据库。** SQLite 没有行级安全，所有 `legal_entity_id` 过滤都靠
    `app/tenant.py` 的入口函数。绕过 API 直接读 `data/oa.db` 的脚本能跨租户 —— 上生产要么换
    PostgreSQL + RLS，要么每个租户独立库。

26. **`system` 是超级身份。** 它能显式跨实体（运维需要），所以 operator token 泄露 = 全租户泄露。
    现在的缓解只有"secret 只在签发时打印一次 + 可撤销"。

27. **记忆隔离只覆盖读。** 写入靠 `identities.employee_id` 绑定员工；没绑定员工的身份
    读写记忆会被 400。但"谁能改记忆的作用域"没有独立审批，agent 自己就能写自己实体的记忆。

28. **岗位级隔离尚未成立。** 同一实体内两个岗位共享 `entity:` 记忆，只有 `pack:` 维度能分开，
    而 pack 目录（`positions.pack_id`）还没有正式的清单与审批。

29. **任务没有列表接口。** 目前只能按 id 取；多 Agent 并行后需要按实体/身份/状态分页检索，
    否则调度只能靠外部记账。

## M3 阶段新增

30. **接力是同步的、跑在 API 进程里。** `/relay/run` 会一直占住这个请求直到两个 Agent 都跑完，
    中途崩了没有断点续跑。要上生产得换成任务队列 + worker（现在的锁已经为那一步准备好了）。

31. **锁是"带 TTL 的租约"，不是分布式锁。** SQLite 单文件够用，但它保护的是"同一个库"；
    多机部署时得换成 Redis/etcd，否则跨机不互斥。

32. **抢锁失败没有排队。** 现在直接 409 回给调用方；调度器要自己做重试/退避（未实现），
    也没有公平队列（后到的可能一直抢不到）。

33. **接力没有共享看板。** 两个 Agent 之间只通过"复制产物文件 + 任务记录"交接，
    没有中间的联合工作区；下一步多业务线并行时会需要一张跨 Agent 的任务板。

## M4 阶段新增

34. **报表只有用量，没有钱。** token 是事实，钱是按各模型单价折算的推断 —— 现在没接价格表，
    所以看板只报 token。接真实成本前不要拿它做预算考核。

35. **业务包没有版本。** `packs` 只有 id/模板/默认预算，改模板会影响所有历史任务的解释口径；
    正式用起来需要 `pack_versions`，任务记录当时用的是哪一版判据。

36. **工作台的密钥存在 sessionStorage。** 内网自用够，但它等价于长期凭据：XSS 能直接读走。
    要更稳得换短时 token（登录换签、TTL 15 分钟），未做。

37. **报表不分页也不缓存。** 数据量上去后 `/reports/usage` 会变慢；现在是全量聚合，
    上规模要加时间窗默认值和索引（已有 idx_audit_task/idx_audit_run，tasks 还没按 pack 建索引）。

38. **工作台只覆盖决策与验收两个动作。** 建任务、派单（relay）、看记忆都还没进 UI；
    目前仍要靠脚本或 curl。

## 集团演练阶段（M5）新增

39. **工具策略把相对路径判成越权。** DSH 以任务 workspace 为 cwd，写 `article.md` 本该落进
    沙箱，但 `_inside()` 用 API 进程的 cwd 去 resolve 相对路径 → 误判 deny、整个 run 被 cancel。
    已改为相对路径按 workspace 解析（`app/policy.py`），并补了越权样例测试。
    教训：安全策略的"路径基准"必须显式，不能吃进程 cwd 的默认值。

40. **代码类任务 30k 预算不够。** 抓行情脚本实测 24,658 tokens 仍被 30,615 的旧轮次顶爆
    （含一次超预算取消）。现在演练按包给预算：content 30k / code 45k；但这是经验值，
    不是按任务复杂度算的，M6 要改成"按历史分位数"派预算。

41. **集团 CEO 能向下读，不能向下写。** `tenant.visible_entities` 只对 `ceo` 且
    `governance_mode='group_direct'` 的实体开放子实体读权限；建任务、派单仍钉死在自己实体。
    风险：报表与看板会聚合子公司数据，若集团与子公司存在竞争关系，需要 `data_boundary='isolated'`
    的真实隔离（现在只标了字段，没实现读取屏蔽）。

42. **演练数据混在同一个库里。** 董事会看板现在能看到 24 个任务，其中大量是调试残留；
    真实演练需要"演练账套"（独立 DB 或 `drill_id` 维度），否则报表可信度会被历史噪音稀释。

43. **判据模板的三处误杀（演练现场暴露）。**
    (a) `_target_words` 对「400-600 字」先匹配到「600 字」再 ×1.6 → 变成 600–960，把 420 字的合格稿判死；
    (b) 代码任务验收取「最新产物」，脚本运行产生的 `prices.csv`(101 B) 比 `fetch_quote.py`(6.5 KB) 新 → 判成文件不可用；
    (c) 关键词判据写死 `requests`，而脚本用的是 `urllib` → 判成「未包含 requests」。
    已修：范围解析优先、产物按扩展名钉住、网络请求改为关键词表。
    **教训：判据是快照，改模板不会追溯已有任务** —— 需要一次显式的判据迁移（本条就是），
    否则「修了规则但老任务永远不过」。M6 应把「判据版本」写进任务，迁移脚本按版本号跑。

## 企微接入（M6）新增

44. **回调里同步调自己的 API 会死锁。** `/wecom/callback` 是 async 处理器，
    在里面直接建任务 → 再发一个 HTTP 请求回本进程 → 事件循环被占住，嵌套请求永远等不到。
    已改为 `asyncio.to_thread` 执行；这也是企微 5 秒超时的硬约束。

45. **token 预算申请被记进人民币预算。** 申请 15 万 token 的决策把 10 万 CNY 预算占用成
    「可用 -50000」。已按币种分流：非 CNY 一律 `non_monetary`，只走规则路由，不动资金额度。
    历史那条数据还留在库里（负可用被钳到 0），没有回滚。

46. **推送去重按 (kind, target_id, user)。** 任务被驳回后重新提交不会再推一次卡片；
    需要「第 N 次进入 pending_review」的版本号才能重推。

47. **中间件从库里读身份明文密钥代签。** 与 §20 同源问题；换 KMS/短时 token 时要一起改。

48. **watcher 直连 SQLite 读状态。** 单机没问题，但把库搬到远端后必须改成调 API，
    否则通道和主程序的数据视图会分叉。

49. ~~**回调只校验了 msg_signature，没有防重放窗口。**~~ **已修（M6b）**：`wecom_seen` 表按
    `msg:<MsgId>` 幂等，事件类消息没有 MsgId 时用 `FromUserName|CreateTime|Event|EventKey` 兜底，
    重复投递只 ack 不执行。保留 14 天自动清理。

50. ~~**公网入口没做白名单。**~~ **已修（M6b）**：`WECOM_IP_ALLOWLIST=1` 时按
    `getcallbackip`（腾讯出口段，缓存 1h）+ `WECOM_IP_RANGES` 校验来源 IP；
    隧道场景下取 `CF-Connecting-IP` / `X-Forwarded-For` 首跳。
    **注意**：翻成 1 之前必须已有 CorpID+Secret，否则取不到企微出口段 → 全量 403。

51. **quick tunnel 的域名每次重启都变。** cloudflared trycloudflare 免费隧道不给固定域名，
    重启后要回企微后台改回调 URL。要固定地址得用命名隧道（需要 Cloudflare 账号 + 域名）。

52. **隧道来源 IP 依赖 `CF-Connecting-IP`。** 走 Cloudflare 时该头由边缘写入、客户端伪造无效；
    但若中间再加一层反代/ngrok，头会被覆盖成隧道自身地址，白名单就会失效——换通道时要重新验证。

## 真机接通（M6c）

53. **template_card 必须带唯一 `task_id`。** 少了它企微回 `errcode 42014 taskid has existed or
    empty or exceed`，卡片发不出去——mock 模式永远发现不了，只有真机第一次发送才暴露。
    已用 uuid 生成，逐条唯一。

54. **应用启用了「企业可信IP」时，一切业务 API 都回 60020。** `gettoken` 却能成功，很容易误判
    成「密钥没问题」。真实出口 IP 要加到 应用管理 → 自建应用 → 企业可信IP，加完立即生效。
    ⚠️ 本机是家宽动态 IP（当前 120.229.58.160），**换 IP 后必须回后台更新**，否则通道静默失效。

55. **watchdog 的 pending 行会卡死重推。** 推送账本先 claim 再 send，若 send 抛错而没写回
    failed，该行永远停在 pending：主循环认为已 claim 不再发，重试逻辑又只捞 failed。
    已改为发送异常即落 failed（带错误文本），并把超过 2 分钟的 pending 也纳入重试。

56. **迁移要写进 MIGRATIONS 清单。** 给 `wecom_pushes` 加 `created_at` 时我先写了个平行机制，
    与已有的 `_migrate` 重复；已合并。SQLite 的 ALTER 不能带非恒定默认值，补列后要单独 UPDATE 回填。

## §44 Octo 桥接层的四个坑（2026-10-18）

1. **Bot 事件队列是死的**：`/v1/bot/events` 永远返回空。入队点只有
   `bot_joined_group` 与内部 `POST /v1/internal/bot-mentions`，而随包服务里没有
   一个调用后者（matter 只调 `/v1/internal/notify`）。桥接层因此改为 tail
   MySQL `message*` 分片（payload 是明文 JSON），每表一个游标。上生产若有服务
   接上 mention ingress，可把 `OCTO_INBOX` 切回 `events`。
2. **建群不带 `space_id` 就是隐形群**：MySQL 有行、成员也在，但 IM 层不认，
   界面「群聊」永远是空。必须带当前空间 id（`OCTO_SPACE_ID`）。
3. **Bot token 长度必须是 35 字符**（`bf_` + 32 hex）；32 字符的假 token 会被
   `auth_failed` 拒掉，而错误信息只说「机器人认证失败」，不提示长度。
4. **Bot 不能拉 Bot**：`createGroup` / `members/add` 只收人类成员，
   所以「Agent 自主拉群」必须由人类 token 代建（`OCTO_HUMAN_TOKEN`）。

另：`_bot` 后缀不是可靠的机器人判据（demo bot 叫 `fe_bot_demo`），
判「来信者是不是 Bot」要查 `octo_bots` 表，否则 Bot 的话会被当成人类需求去建任务。

## §45 真模型（ACP）的预算粒度与重跑成本（2026-10-18 实测）

1. **预算闸的粒度是「一轮」，不是「一次调用」**。HTML 落地页任务一轮就吐 ~45–51k tokens，
   所以 cap=30,000 的闸在 44,871 处才熔断（`budget_exhausted_midturn`，exit 3），
   45,000 的闸在 50,978 处熔断。要跑通这类任务，cap 至少 60k；「零超支」在 ACP 上做不到，
   能保证的是「超支上限 = 一轮」。
2. **重跑很贵，但续跑会便宜**：同一任务三次运行分别 51,198 / 50,978 / 21,836 tokens。
   第三轮因为 `session/resume` 复用了上下文（模型只做定点修改），成本降到 42%。
   ⇒ 打回重做时应尽量 resume，而不是新开 session。
3. **判据抓到了真实违规**：第一版页面写了「100%」，被 `banned` 判据当场拦下（广告法禁用词），
   重跑后通过。说明 L0 规则在真模型输出上有效，不是只对 mock 有效。
4. **桥接重试的身份要用同一个**：用 `client_from_env()` 重试时如果环境里是另一个 identity，
   产物会落到另一个 workspace，验收取到的是新文件、旧目录里留着上一版 —— 排障时容易误判。

## §46 企微原生审批：一个授权开关挡住了所有调用（2026-10-18）

- 建模板的正确接口是 `POST /cgi-bin/oa/approval/create_template`（文档 path/97439），
  不是 `addtemplate`（404）。提交审批 `POST /cgi-bin/oa/applyevent`，
  查状态 `GET /cgi-bin/oa/getapprovaldetail?sp_no=`；
  `getapprovaldata` / `gettemplatelist` / `gettemplate` 在本企业均返回 404。
- 首次调用 `create_template` 返回 **48002 api forbidden**：自建应用必须先被加入
  企微后台 → 审批 → **「可调用接口的应用」**，否则所有 oa/* 写接口都不可用。
  这是控制台开关，代码绕不过去。
- 因此审批链路设计成「出站轮询」：我方 `applyevent` 建单 → 董事长在审批 App 点同意
  → 轮询 `sp_status==2` 后写 `checked_by/note` 并重跑验收。**不需要公网回调**，
  隧道轮回也不再影响签字（与 §51 的 quick tunnel 问题解耦）。
- 模板控件 id 必须是 `Text-01` / `Text-02` 这种「类型-序号」格式（`task_id` 会报 controlId_format_err），改模板要同步改
  `app/wecom/approval.py` 的 `apply_data.contents`。
- 兼容性设计：部分企业的 `/cgi-bin/oa/gettemplatelist`、`gettemplate`、`getapprovaldata`
  被网关屏蔽（直接 404），所以系统采用 **create_template → sp_no → 逐单 getapprovaldetail**
  的轮询路径，不依赖任何列表/模板查询接口；这也让「只知道自己建过哪几张单」成为可接受前提。

## §47 字数指令不服从：模型稳定输出 ~300–420 字（2026-10-18 实测）

同一条「秋季护肤短文」，要 450 字得到 422 字、要 600 字得到 396 字、要 300 字得到 301 字。
即模型对「请写到 550-650 字」这类**数字指令不敏感**，却对**任务文本里的目标值**敏感
（`_target_words` 从标题取范围，标题写 300 就拿 300-480，正好命中）。

结论与做法：
1. 判据字数区间应由任务文本决定，不要指望 prompt 里的补充指令——它不生效；
2. 需要更长正文时，正确做法是**改任务标题**（如「800 字」）或分次扩写，
   而不是在 retry prompt 里要求「补到 600 字」；
3. 反例留档：`T-22d16160f6db433b`（要 450 字，三轮 669/422/396 字，全部 `rejected`，耗 7,039+15,178+17,026=39,243 tokens），
   它证明「靠 prompt 改长度」是无效路径。

## §47 字数判据 + 自动扩写修复环（M10）

**现象**：同一提示词下模型输出稳定比目标少 15–25%（200 字要 210、500 字要 473），
L2 字数判据直接把本来合格的内容打成 `rejected`，送人工。

**做法**：验收失败后不直接交人，先让同一 Agent 在同一 session 里补写一版：

```
run → acceptance → 字数不在区间 → repair_attempt(记审计, 带精确目标字)
    → 同 session resume 重跑 → 再验收
    → 两次仍不达标才 rejected 交人工
```

- 目标字 = 区间中点（有上界时）或 `min * 1.2`（无上界时）。
- 修复预算独立 `REPAIR_BUDGET`（默认 30k），且**按实体池剩余额度收敛**，
  否则 `reserve_budget` 会直接 `denied`，run 连启动都启动不了（本轮踩到）。
- 实测一次修复：473 → 670 字，7,406 + 23,535 tokens，同 session（5f8648e4…）续跑。

**坑**：修复成功后任务状态仍停在 `rejected` —— 第一次验收把状态写死了，
第二次验收的 `pending_review` 没能覆盖。修法：修复后不再读旧状态，
而是**按判据重算**（有 blocking failed → rejected；有 pending → pending_review；否则 completed）。

## §48 群聊路由与防死循环（M10）

- 群成员角色：`pm`（第一个）/ `member` / `reviewer`（第三个及以后最后一个）。
  早期只写 `pm|member`，导致 PM 把活派给了 reviewer —— 角色必须显式落库。
- 发言顺序：PM 拆解并 @ 被指派人 → 被指派人「收到，马上开始」→ 执行 → 卡片 → reviewer 复核。
- **防死循环**：`group_hops` 里 10 分钟窗口最多 4 次 agent 发言，超了群里直接静默
  （不是报错）。演示时曾因窗口用尽而「发了消息没人理」，这正是闸门在起作用。
- 群聊不额外建 LLM 分派器：分派文本用模板 + 任务标题，省一次调用；要更自然的措辞可再接 intent 模型。

## §49 群聊/私聊都要先过意图闸（M10.1）

真机复现：董事长在桌面端群里发了「哈喽」，系统把它当派活 —— 建任务 `T-095e2672d5b643af`、
跑满 28,185 tokens、扩写一次、最后因「关键词 0/3 + 命中禁用词 最好」判 `rejected`。
这正是当初 Octo 桥接被投诉的同一个毛病，搬到自研前端后又犯了一次：
**群聊路由只做了分派，没有做意图判断。**

修法：`_run_group_turn` 与 `_run_turn` 在动手前都调 `app.octo.intent.classify`：

- `chat` → PM 直接用自然语言回一句，**不建任务**（实测「你好，在吗」→「董事长好，我在的，随时听您安排。」，
  任务总数不变 48）。
- `work` → 走原来的拆解 + 建任务。
- 模型不可用时 `classify` 内部回退关键词启发式，不会把消息丢掉。

---

## 封版（v1.0.0）后待办 —— 未实现，等解冻

以下两项是在封版指令之前提出的需求，因「研发阶段收官、除恶性 Bug 外不再加功能」而未动代码：

**A. 注入灵魂（偏好记忆 / 群上下文 / 查询工具）**
- `user_preferences`：从「以后叫我老板」里抽 `boss_title`，写库并注入每次的 system prompt。
- 群聊建任务时把该群最近 10 条消息作为 history 前置。
- 闲聊提问（「我的秘书呢」）应走只读组织架构查询，而不是让模型凭空回答。
- 闲聊走轻量对话、不进沙箱（这一点 v1.0 已用 `intent.classify` 做到）。

**B. 群组与成员管理（管理力）**
- 群聊「…」菜单：邀请成员 / 移出成员 / 退出（群主退出=解散）。
- 组织架构：点 Agent 出详情面板，支持编辑人设与模型、停用（软删除）。
- 左侧底部：设置（模型全局配置、深浅色主题）+ 账号（身份 ID、改密钥）。
- 右上角：帮助（使用说明弹窗）+ 关于（版本/引擎）。
- 底层要求：`member_kicked` / `agent_fired` 审计事件、释放 token 预算、停止运行中的进程、一律软删除。

## §50 新表列必须写 MIGRATIONS，否则老库静默炸掉整条链路（v1.1 踩坑）

给 `chat_members` 加了 `state`（软删除），但只在 `store.DDL` 里写 `CREATE TABLE IF NOT EXISTS`。
老库表已存在 → DDL 不生效 → `store.members()` 的 `COALESCE(m.state,'active')` 直接报
`no such column: state`，异常在后台线程里被吞掉，**群里发消息没有任何反应**，
而 E2E 只断言「没有新建任务」，照样绿。

**规则**：只要是给已存在的表加列，必须同时进 `app/db.py::MIGRATIONS`（ALTER TABLE），
不能只改 schema.sql / DDL。验证方式：`PRAGMA table_info(<table>)`。
**测试要断言正向结果**（AI 真的回话了），不能只断言"没发生坏事"。

## §51 真机 UI 任务在「提权」这一步死掉（v1.2 监控发现）

真机「重构 UI」的开发阶段（T-a960a6744a344607，web-v1，120k 预算）跑了 **74,477 tokens** 后失败，
运行日志里的关键是这一行：

```
Error: sandbox escalation to "danger-full-access" requires approval
```

含义：DSH 的 agent 想升级沙箱权限（几乎可以肯定是为了 `npm install` / 建工程），
但 headless ACP 会话没有任何人可以点「允许」，于是它把同一件事反复重试
（日志里连续 8 次写 index.html），预算烧光、任务失败。

**结论**：在当前 ACP 能力下，**不能让 agent 去装依赖**。两种走法：
1. （已采用）Prompt 硬约束：单文件、Tailwind CDN、禁止 npm/工程化；
2. （未做）给 DSH 配一个允许提权的 profile，但那是绕过沙箱，与「沙箱必须拦越权」的验收冲突，
   需要单独立项，不能在业务任务里偷偷放宽。

## §52 越权审批不能靠「给 Agent 放开目录权限」（v2.3 真机演练）

真机演练 `RUN-c9ef0350bd6c4d34`：DSH 的 `workspace-write` 与 Low 沙箱都把任务目录之外当禁区。
任务工作区里的 `shared/` junction（指向 `workspace/resources`）在字面上位于 cwd 内，但 DSH 按
realpath 判定为「出域」，所以即使审批通过、闸门放行，Agent 的 `write` 仍然被 DSH 自己拒绝，
它随后连续尝试 `danger-full-access` 提权 —— 正好说明「给 Agent 开目录写权限」不可取。

**采用的做法（平台代执行）**：
1. 审批单里保存被拦工具的完整 `rawInput`（path + content）；
2. 批准后由平台把内容逐字节写到已批准的资源目录，路径再做一次 realpath 必须落在该资源内，
   写 `approval_applied` 审计（sha256/bytes）；
3. Agent 用**同一个 run_id + session_id** `session/resume`，prompt 只告诉它「平台已代执行、
   不要重写、读回确认后继续」；
4. 同一 run 内对**同一个已落盘目标**的读回/重试写直接放行（`approval_reused`），
   否则 Agent 读回验证会被当成新的越权，陷入「批准 → 再拦 → 再批准」的死循环。

## §53 审批挂起不能发 run_failed（v2.3）

`finish()` 会发 `run_failed`，任务状态被推进 `failed`；`failed` 只能 `retry`，不能续跑。
新增 `dsh_adapter.park_run()`：只把 `task_runs.state` 改成 `awaiting_approval`、`exit_code=9`，
任务保持 `in_progress`，不发终止事件。批准后用同一个 run_id 重新 `execute_run`，任务
`in_progress` 下直接 `run_succeeded → pending_review`。

一次性审批的消费时机也要注意：在**拦下时**消费会让「工具执行失败后的重试」变成第二张审批单；
现在改成「代执行成功后消费」，并用 `applied_at` 区分「已落盘」与「仅批准」。

## §54 Trace 的 thinking 是整块到达的（v2.3）

DSH 的 `agent_thought_chunk` 往往一次性推完整段思考，`started_ms == ended_ms`，瀑布图全是 0ms。
正确口径：thinking span 的起点取**上一事件结束**（模型等待时间），tool span 取 tool_call 通知时刻。
token 归因不能用 `session_baseline`（它排除当前 run，续跑时会把上一段会话的 token 全记到第一个
thinking 上），要用「本 run 已累计 usage」作为 `last_used` 起点。审批等待用 `approval` span 单独记，
前端默认把审批区间从时间轴剪掉再画瀑布，否则 3 分钟等待会把 10 秒思考压成一条线。

## §55 FinOps：ACP 只回 token 总量，输入/输出必须按比例估算（v2.4）

`usage_update` 只有累计 `used`，没有 prompt/completion 拆分。真实账单无法从本地还原，
所以 `.env` 里同时写输入/输出单价（`MODEL_PRICES_JSON`，元/百万 token），再用
`FINOPS_INPUT_RATIO=0.75` 估输入占比。前端明确标「估算」，不把估算当账单。
每个 Run 的费用按 `usage_tokens - usage_baseline` 算增量；直接拿累计值会把多轮会话重复计费。
`tasks.owner_employee_id` 为空的运行（CEO 调度、产线）归到「未归属」，不要硬塞给某个部门。

## §56 离线评测：预算池不够时先压估，mock 不认 prompt（v2.4）

- eval 任务走真实 ACP 时会先过 `budget_gate`；实体池剩余 12k 而请求 30k 会被拒（exit -2，
  `spawned=false`）。`evals.run_case` 现在把请求压到实体池剩余（至少 4k），并把
  `state=denied` + reason 落库，前端能看出是预算问题而不是「模型没产出」。
- `mock_acp` 永远写 `article.md` 的护肤示例，**不读 prompt**；用它跑真实内容基准会得到
  低分（本次 0.86），只能验证链路，不能当质量结论。质量回归必须 `runner=acp`。
- `prompt_mode=stored` 复用基准当时的完整 prompt（含附件正文），隔离模型变化；
  `prompt_mode=current` 用当前模板重新组装，用来测 Prompt 改版。两者不能混为一谈。
- eval 会建 `task_type='eval'` 的任务；没有验收判据，跑完停在 `pending_review`，这是故意的。

## §57 组织经验：注入块不能自我复制（v2.4）

`pipeline.run_project` 把命中的经验追加到 requirement 后，这条 requirement 会流进
研讨会纪要。`capture_consensus` 必须先用 `split("【组织经验（自动注入）】")[0]` 剥掉注入块，
否则下一轮共识会把上一轮经验再抄一遍，经验库越滚越长。
召回按「标签交集 ×3 + 词面 2-4 字重合 ×0.8 + 近 120 天衰减 + weight」排序；
每条注入内容截到 400 字，默认 3 条，避免 prompt 膨胀。完整内容留在库里供 UI 查看。


---

## （v3.0–v3.2 补齐：§58–§68，2026-10-20 从本地档案合入主线）

> 本节起为 v3.0/v3.1/v3.2 的实测结论。v3.2 的四条在原档案里沿用了 §60–§63，
> 与 v3.1 同号；合入主线时按序改为 §65–§68，并在标题里保留原编号以便回溯。

## §58 组织经验的人工审核（v3.0）

`org_experiences.state`（pending/approved/rejected）是**唯一注入闸门**：
`context_for()` 只查 `state='approved'`。升级时老库用 `ALTER ... DEFAULT 'approved'` 回填，
避免上线瞬间经验全空；新沉淀在 `add()` 里显式写 `pending`。
审核写 `experience_reviewed` 审计（reviewer/state/note），驳回原因保留给下一轮沉淀参考。
**不要**用「自动沉淀即生效」当默认——AI 总结错一次，会污染之后所有立项。

## §59 MCP 外部工具：走 ACP `session/new.mcpServers`（v3.0）

DSH ACP `initialize` 只声明 `mcpCapabilities.http=true`（没有 stdio/sse），所以客户端只传
HTTP 形态：`{type:"http", name, url, headers:[{name,value}]}`。实测 `session/new` 接受后，
DSH 会把工具暴露成 `mcp__server_<hash>__<tool>`（本次 `mcp__server_8d629141__send_email`），
`tool_call` 通知照常进 Trace/RBAC 闸门，无需改 runner 的工具循环。
- 绑定是**按员工**（`agent_mcp_tools`），不是按角色模板；Run 启动时查一次并写 `mcp_attached` 审计。
- 邮件 MCP 的 `send_email` 在未配 SMTP 时只写 `data/mail_outbox/*.eml` + `sent.jsonl`，
  返回文本会明确写 `outbox`。**验收时必须区分「工具调用成功」与「真实投递成功」**。
- 密钥存 SQLite（本机单用户），接口只回 `has_key`；SaaS 化前要换 KMS/Keychain。

## §60 多租户预埋：先做「有 tenant_id + 请求上下文」，不是完整隔离（v3.0）

`tenants` + `identities/projects/chats/mcp_servers.tenant_id` 已落库，老数据回填 `TENANT-HQ`。
登录返回 tenant_id，前端把 `X-Tenant-Id` 加到每个请求；`_tenant_of()` 用身份自己的租户校验，
**外来 header 直接 403**（不是 404，避免掩盖配置错误）。
当前强制点：chats 列表/消息/发消息、projects 列表/详情、mcp 列表/新增。
其余任务/Run/文件仍走 `legal_entity` 可见域——SaaS 前必须把 claims、后台任务、文件下载
全部收口到 tenant，否则知道 ID 就能跨租户读。别把「预埋」说成「多租户已完成」。

## §61 报错必须说人话，且卡片上要有下一步（v3.1）

机器码留在 API/审计里，UI 只在边界翻译。前端 `humanize()` + `api.js` 的 `ERROR_TEXT` 双层兜底，
覆盖 `GateNotPassed / build failed / acp_timeout / budget_exhausted_midturn / pending_review`。
任务卡失败态给【一键重试】（同 session 新 run，不重跑整段 prompt），挂起态给【去审批】；
项目卡的失败闸门给【复核通过】，通过后项目解冻、阶段补 done，再由【一键重试】继续剩余阶段。
**不要**在聊天里直接抛 `str(exc)`；dispatcher 的 `human_error()` 已接管。

## §62 SMTP 配置：MCP Server 每次调用读 DB，不读启动时环境（v3.1）

`settings` 表存 `smtp_host/port/user/pass/from/secure`；`tools/mcp_email_server.py` 每次
`tools/call` 现读 DB（env 仍优先，供 standalone 调试）。所以设置面板保存后无需重启 MCP。
- 配了 SMTP 但发送失败：直接返回 `isError:true`，**绝不静默退回 outbox** 冒充已发。
- 未配置 SMTP：才写 `data/mail_outbox/*.eml`，返回 `transport=outbox`。
- 密码只存服务端，`GET /ui/smtp` 只回 `pass_set`；审计 `smtp_saved` 不写密码。
- 实测：Ethereal 真实 SMTP（587/STARTTLS）发出 3 封并用 IMAP 收到；QQ/163 只要把
  host 换成 `smtp.qq.com`/`smtp.163.com`、账号换邮箱、密码换**授权码**即可。
- 端口 465 勾选 SSL，587 不勾（STARTTLS）；发件人一般与账号一致，否则 163/QQ 会拒。

## §63 tenant 收口到 tasks / task_runs / artifacts（v3.1）

新增列：`tasks.tenant_id`、`task_runs.tenant_id`、`artifacts.tenant_id`、`tool_approvals.tenant_id`。
写入端从项目/任务/运行继承，`init_db()._backfill_tenants()` 每次启动同步（项目存在时以项目为准）。
读取端：`/ui/tasks/{id}`、`/ui/runs/{id}/trace|kill|live`、`/ui/files*`、`/ui/approvals*`、
`/ui/finops`、`/ui/designs/*`、`/ui/projects*` 全部按身份 tenant 过滤；跨租户一律 404。
`legal_entities` 也补了 tenant_id，`tenant.visible_entities()` 对非 system 只返回同租户实体，
所以签名 `/api/*` 的 entity 作用域也间接收口。**残留**：组织经验/评测集是组织级共享，未按租户切。

## §64 公司红线：手工经验 + 每次 Run 强制注入（v3.1）

`org_experiences.mandatory=1`：手工新建时立即 approved（董事长本人签署），
`context_for()` 无视标签/实体召回（分数 999），`experience.red_lines()` 再由
`dsh_adapter.execute_run()` 追加到**每个 Run 的 prompt**（含重试/评测/WBS），
审计 `red_lines_injected`。上限 8 条、每条截 240 字，防止 prompt 膨胀。
**不要**只靠标签召回实现红线——标签没命中就漏，红线必须无条件注入。

## §65 Role Alignment（v3.2，原编号 §60）
岗位职责边界不是靠 RBAC 兜底，而是在 Prompt 里先“说清楚”：`app/role_alignment.py` 把角色模板 tag 与岗位/员工标题映射成 dev/frontend/backend/qa/audit/legal/architect/content/product/finance/ops/data，再在 `dsh_adapter.execute_run()` 里追加【职责边界声明】，研讨会发言也走同一份边界。
注意：标题含“测试开发”的人不能同时吃 dev 边界；已加去重规则，QA 只保留测试边界。Prompt 是软约束，真正的物理拦截仍在 RBAC/审批。

## §66 企业红线 L0（v3.2，原编号 §61）
红线来自 `org_experiences` 中 `state='approved' AND mandatory=1` 的记录，`redline_terms` 是逗号分隔禁用词；`acceptance_run` 在常规判据后扫描**文本产物**，命中即新增 L0 failed 判据并直接 rejected，没有人工审批入口。
当前只扫描能解码为文本的产物；图片/PDF/二进制未做 OCR，红线词也不做同义词扩展（如“全网最低”不会自动匹配“全网最低价”之外的说法），需要人工维护词表。

## §67 商业接单演练（v3.2，原编号 §62）
`tools/v32_commercial_order.py` 是真实演练脚本：四个角色各跑一次 ACP，前端写 `index.html`，后端写 `api_contract.md`，文案写 `delivery.md`，测试写 `test_report.md`，再由 MCP 邮件网关带附件走真实 SMTP。
它为了让演示稳定，WBS 四条和 build/test 闸门由脚本显式落库，没有复用 `execute_build` 的通用派单；但 ACP Run、产物落盘、验收判据、邮件投递都是真的。生产派单仍以 `pipeline` 的 WBS/任务生成路径为准。

## §68 触发器幂等（v3.2，原编号 §63）
`pipeline.ensure()` 每次项目详情都会调用；原实现先 DROP 再 CREATE 触发器，并发请求会撞 `already exists` 导致 500。已改为 `CREATE TRIGGER IF NOT EXISTS`，不再每次删除重建。

---

## （v5.4 补齐：§69–§75，2026-10-26 生产库归因与并发实测）

> 本节起为 v5.4 的台账。§69–§73 是「从失败数据反推物理根因」的产物，修复分别落在 v5.2 / v5.3，
> 这里补齐记录，避免下一轮把同一个坑当新问题再查一遍；§74 是闸门被流程推进器代签的设计级缺陷
> （v5.4 已修复并回归，全量 626 passed / 0 failed，见该节）；§75 是口径警告，不是缺陷。
> 每条按「现象 / 物理根因 / 修法 / 如何避免重踩」四段记。

## §69 L0 红线扫描的自指陷阱（v5.4 记录，修复于 v5.2）

**现象**：生产库 501 个 `web-v1` 任务里 431 次红线失败，抽样归因 40/40 全部落在任务自己写出的
`test_check.py`，驳回理由高度集中为「命中企业红线：100%」。而这些任务交付的落地页本身是干净的。

**物理根因**：自检脚本按设计**必须**硬编码它要检的禁用词清单（`banned = ["100%", "绝对", ...]`），
而 `acceptance_run.validate_mandatory()` 把 `artifacts` 里的每个文本文件都当成「对外文案」送去扫红线。
它不区分「被测对象」与「测量工具」，于是工具自己命中了它自己要检的词 —— 命中是逻辑必然，与模型能力无关。
这是验收层的自指（self-reference）缺陷，不是内容质量问题。

**修法**：`app/acceptance_run.py:21-23` 增加工具文件正则

```python
_TOOLING_FILE = re.compile(
    r"^(?:test_.*|.*_test|.*_check|check_.*|verify_.*|lint_.*|conftest)\.py$", re.I)
```

扫描循环里命中即 `continue`，并把跳过的路径收进 `skipped_tooling`，最终写进
`redline_scans.snapshot["skipped_tooling"]` 与 `redline_checked` 审计 payload
（`acceptance_run.py:134 / 146-148 / 215`）。跳过不是静默的 —— 审计里能查出「跳过了哪些文件」。
回归用例：`tests/test_v52_redline_self_reference.py`（交付页干净 + 自检脚本含禁用词 → 必须放行）。

**如何避免重踩**：任何「扫描 / 校验 / 统计」类组件上线前先问一句
**「它的输入里会不会包含测量工具本身」**。工具文件必须显式白名单豁免，且豁免要留痕（进快照/审计），
不要靠「让工具脚本里别写禁用词」这种口头约定 —— 那等于让测量工具自废武功。
新增工具类文件命名（如 `*_spec.py`、`assert_*.py`）时必须同步扩这条正则，并补一条「干净交付 + 脏工具」的用例。

## §70 落地页模板与自检脚本的契约不对齐（v5.4 记录，修复于 v5.2）

**现象**：`_render_finops_html()` 生成的落地页从来就没有 `id="btn-calc"` 的行动按钮，
而同一内核生成的 `test_check.py` 恰好断言 `check('id="btn-calc"' in raw, "action button: btn-calc")`。
自检因此永远 FAIL → 内核重写一版同样内容 → 还是 FAIL → 交付物始终没有 CTA →
L0「含可点击的行动按钮」必然驳回（生产库 438 次同一失败）。CTA 从未真正交付过。

**物理根因**：模板和自检脚本是同一份契约的两端，却由两段互不引用的字符串字面量维护：
模板在 `_render_finops_html()`，检查项在 `_render_test_check_py()`，没有任何机制在模板变更时重跑自检。
缺的不是模型能力，是模板自己和自己打架。

**修法**：模板补齐真实按钮 `<button type="button" class="cta" id="btn-calc">立即测算</button>`
（`app/engine/runner_native.py:880`，底部同 id 的操作条见 `:920`）与对应 JS handler
（`document.getElementById('btn-calc')` → `recalculate()` 并滚动到结果区，`:1045-1052`）；
自检脚本里的禁用词改成 `chr()` 动态拼装，避免它自己再触发静态扫描（`:1132-1137`）。
实测：拿模板产出跑它自己生成的自检脚本 → `PASS 24 / 24`、`exit 0`。
回归用例：`tests/test_v52_native_landing_cta.py`。

**如何避免重踩**：生成器与验收脚本必须**同源可测**。模板类交付物的最小回归是
**「拿模板跑一遍它自己生成的自检」**，而不是只断言模板里有 `<button>`。
改模板就必须重跑自检；自检项增减时，模板与测试要一起动。

## §71 异步后台线程的 DB_PATH 寻址漂移（v5.4 记录，修复于 v5.3）

**现象**：`POST /ui/projects` 与 `resume` 的自动点火线程会偶发写到**别的库**：
测试里下一个用例改了 `DB_PATH`，上一个用例的线程才醒来执行，于是把数据写进新用例的库，
既造成跨用例污染，又让新用例建库时撞 `database is locked`。表现为「随机失败、重跑就好」。

**物理根因**：`db_path()` 按设计在执行时读环境变量（为了测试能切库），而
「请求处理」是同步的、「流水线推进」是异步的。两者之间隔着一个可变的进程全局 ——
线程执行时才调 `get_conn()`，读到的是**执行时**的 `DB_PATH`，不是**发起时**的那个库。
寻址漂移的本质，是把「上下文」存进了全局变量。

**修法**：请求入口就把目标库固定下来：`owner_db = db_path()`
（`app/ui/routers/projects.py:84`、`:543`），后台线程改用 `db_connect(owner_db)` 绑定
（`:89`、`:547`）。`app/db.py:181-196` 另提供 `open_conn(path)` 上下文管理器，
`notify` 的后台线程用同一模式（`app/notify.py:73 / 78 / 87`）。
生产只有一个库，行为完全等价；差别只在「进程内换库」这种测试/演练场景。

**如何避免重踩**：任何跨线程、跨进程的持久化工作，都把**目标库 / 目标文件当作显式参数，
在创建线程处绑定**，不在 worker 内部读环境变量。自查手段很土但有效：
把所有 `def _worker` 都 grep 一遍，看里面有没有 `get_conn()`。

## §72 `PRAGMA journal_mode=WAL` 的排他锁竞态（v5.4 记录，修复于 v5.3）

**现象**：并发请求或测试并行时偶发 `sqlite3.OperationalError: database is locked`，
失败点在建连接、切 WAL 这一步，与业务 SQL 无关。

**物理根因**：`PRAGMA journal_mode = WAL` 是**需要短暂独占锁**的库级操作，
而 SQLite 的 busy handler 对这条 PRAGMA **并不总生效** —— 进程内其它连接正在读时，
切换会立刻返回 `SQLITE_BUSY`，`busy_timeout=30000` 也救不了这一步。
同时 WAL 是**文件级持久属性**：一旦某个连接切成功，后续连接读到的就是 wal，
所以「这次切失败」不等于「这个库不是 WAL」。

**修法**：`app/db.py:22-44` 的 `_enable_wal()`：每轮先试
`PRAGMA journal_mode = WAL` 看返回值，失败再**读当前模式**确认（可能别人已经切好了）；
6 次短退避（`0.05s × (attempt+1)`）仍拿不到锁就**显式抛错**
`cannot enable WAL journal mode (database is locked)`。
绝不静默退化成 `delete` / `truncate` 模式 —— 那会破坏「WAL 单写」这条硬不变量，而且事故现场无痕。

**如何避免重踩**：凡是「需要独占锁」的 PRAGMA / DDL，一律
**先试 → 再查现状 → 短退避重试 → 最后显式失败**；
不要用 `try/except: pass` 把锁冲突吞掉。被吞掉的那一次，就是将来最难查的那一次。

## §73 `task_runs.runner` 的审计归因回写（v5.4 记录，修复于 v5.3）

**现象**：用 NativeKernel 跑出来的任务，`task_runs.runner` 里写的却是 `acp`。
Trace、FinOps 按 runner 分组统计、排障时「到底走的是 ACP 还是本地内核」全部归因错，
生产库里这一列一度失去区分度。

**物理根因**：`runner` 是**建行时的假设**，不是**执行后的事实**。
建 run 行发生在派发之前（`pipeline.py:923`、`:1293` 都硬编码 `'acp'`），
此时 `kernel_router` 还没决定路由；真正执行的内核名字在 `out["runner"]` 里，
但只有「真的拉起 ACP 子进程」那条分支会回写，本地内核路径没人补。

**修法**：两处 `execute_run()` 之后按执行结果回写真实内核：

```python
if out.get("runner") and out["runner"] != "acp":
    conn.execute("UPDATE task_runs SET runner=? WHERE id=?", (out["runner"], run_id))
```

（`pipeline.py:949-953` 与 `:1314-1318`）。stub 分支的 run 建行时就直接写
`runner='stub'`（`:1265-1269`），让「离线模拟」和「真实 ACP」在审计里一眼可分。

**如何避免重踩**：审计字段凡是「执行前未知、执行后确定」的，都必须有**回写点**，
并把「建行时的值只是占位符、最终以执行结果为准」写进注释。
新增一条内核路由（比如再上 cloud kernel）时，先检查回写点覆盖到了没有。

## §74 `flow_stage_accept` 伪造 L1 签批后门（v5.4，已修复并回归）

**现象（v5.4 之前的实现）**：流水线进入 `accept` 阶段时，`pipeline._flow_verdict()` 会把这个任务**所有**
blocking 判据直接刷成通过：

```python
conn.execute("UPDATE acceptance_criteria SET state='passed', note='flow_stage_accept'"
             " WHERE task_id=? AND is_blocking=1 AND state IN ('pending','failed')", (tid,))
```

（v5.4 之前的 `_flow_verdict()` 实现；这段无条件刷绿的代码已在 v5.4 删除）。于是
`human_check` 这类 L1 判据在**没有人提交任何 `evidence_snippet`**
的情况下就成了 `passed`，任务随之 `pending_review → completed` —— 等于让模型自评通过，
闸门本身被绕过了。行为还不统一：`web-v1` 在 `build` 阶段就被 L1 卡在 `pending_review`，
其它 pack 却能一路自动过。

**物理根因**：「验收」在实现上被写成了「把判据刷绿」，而不是「核对证据」。
`acceptance_criteria.state` 是唯一物理真相，但写它的入口有两个：验收器（有证据、有 L0/L1/L2 分层）
和流程推进器（只要阶段跑完就无条件刷）。后者为了让「全流程能跑通」拿到了无条件的写权，
于是把「人工验收」这个语义整个跳过去了。这是设计级缺陷，不是某个测试的锅。

**修法（v5.4，代码侧已落地）**：

- **规则**：生产路径下进入 `accept` 时检查项目下所有 blocking L1，仍有未签批项则**严禁**置
  `passed`，`accept` 阶段挂起并发出待验收通知；人类通过 `/criteria/{id}/check` 附
  **非空 `evidence_snippet`** 补齐后，任务才 `pending_review → completed`，并自动走完
  `release` / `retro`。仅 `M0_AUTO_SIGN_L1_FOR_TEST=1`（生产严禁开启）的测试路径放行。
- **`acceptance_run`（判据层）**：新增 `pending_human_signoffs()`（项目级待签批清单）、
  `task_pending_human_signoffs()`、`awaiting_human_signoff()`（「L0/L2 全绿、只差 L1」的判定单元）、
  `task_ready_to_settle()`、`auto_sign_pending_l1()`（任务级）、`auto_sign_project_l1()`（项目级，
  进 `accept` 前统一放行仅测试通道的遗留 L1）；`run()` 末尾的自动签批已收口到
  `auto_sign_pending_l1()` —— 只有 `M0_AUTO_SIGN_L1_FOR_TEST=1` 才生效，且必然写
  `l1_auto_signed_for_test` 审计、`checked_by='test-auto'`（开了就赖不掉）；返回值新增
  `awaiting_human`，调用方不用猜（`app/acceptance_run.py:248-340 / 423 / 482`）。
  **一个自己踩过的坑**：早期版本用 `state NOT IN ('passed','na')` 选待签批行，会把**人工显式驳回的
  `failed` 判据也翻成 passed** —— 测试通道也不该覆盖人的否决。已收紧为 `state='pending'`。
- **`pipeline._flow_verdict()`（阶段层）**：SQL 条件收紧成
  `is_blocking=1 AND check_type<>'human_check' AND state='pending'`，自动判定（L0 红线 / L2 脚本）
  只在「还没判定过」时按阶段闸门放行，但 L1 绝不由它代签；随后只在测试通道调
  `auto_sign_pending_l1()`；仍剩未签批项就 `return "pending_review"`，任务不再被推进 `completed`
  （`app/pipeline.py:1008-1032`）。
- **同族残留（本轮一并收口，属隐患而非线上事故）**：原 `_flow_verdict` 还有一句
  `state IN ('pending','failed')`，会把**已经判失败**的 blocking L0/L2 判据也翻成 passed ——
  这是「自动放行失败判据」，与「严禁模型自评通过」同源。已收紧为 `state='pending'`，
  并补一道兜底：仍存在 `check_type<>'human_check' AND state='failed'` 的 blocking 判据时，
  直接 `raise RuntimeError("…blocking 自动判据未通过，不得放行")`，不再无声收尾
  （`app/pipeline.py:1011-1027`）。**如实说明**：现有流程里阶段进入时自动判据的初始态就是
  `pending`，`'failed'` 分支实测不可达 —— 所以这不是修一个正在发生的 bug，而是堵掉一个
  「一旦被激活就是事故」的隐患；代价是这条收口**没有线上事故可追**，别把它写成「修复了线上错误」。
- **`accept` 阶段（挂起而非失败）**：进入 `accept` 先走 `auto_sign_project_l1()`（仅测试通道，
  生产环境直接返回 0），再查 `pending_human_signoffs()`；非空则写「等待人工签批」的
  `06_accept.md`、`_accept_gate(passed=False)`、`_request_human_signoff()`（通知/企微签名请求，
  发不出去不影响挂起），再抛 `HumanAcceptancePending`；上层把 `project_stages` / `projects`
  置 `awaiting_accept`，**不是** failed/blocked
  （`app/pipeline.py:587-592 / 728-755 / 1572-1590`）。闸门行用 `_accept_gate()` 做 upsert
  （`:598-614`）：挂起 → 签批 → 通过会多次进入 `accept`，每次都插新行会让旧的 failed 行把
  `trg_stage_gate_guard`（阶段置 done 前不得存在非 passed 闸门）永久卡死，所以复用同一行翻转状态。
  **挂起页上的签批入口必须写真实可用路径**：`06_accept.md` 里给的是
  `POST /criteria/{criterion_id}/check` —— 这个仓库的路由没有 `/api` 前缀，写成
  `/api/criteria/...` 人照着操作会直接 404（这行文案后来单独修过，`:742`）。
  给人看的接口地址必须和路由表一致。
- **签批后自动收尾（规则 3）**：`POST /criteria/{criterion_id}/check` 走 `l1_manual.apply`
  （缺 `evidence_snippet` 直接 400 `EvidenceRequired`），commit 之后调
  `pipeline.maybe_resume_after_signoff()`（`app/api/tasks.py:140-165`）：先 `settle_signed_tasks()`
  （只结算 `task_ready_to_settle()` 的任务，`review_passed` 的 actor=`l1_signoff`），
  再仅在「已无待签批 + 项目确实停在 `awaiting_accept`」时唤醒 `release` / `retro`
  （`app/pipeline.py:1610-1670`）。唤醒线程同样绑 `owner_db`（见 §71）。
- **与计划原文的一处有意偏离**：计划 Action Step 2 写的是「阶段保持 `active`」，实现为
  `project_stages.state` / `projects.state` = `awaiting_accept`。理由是 `finance.mark_stage`
  早有 `awaiting_budget` 这种非标准状态做先例，`awaiting_accept` 能让 UI 与续跑逻辑无歧义地区分
  「正在跑」与「等人签」，而且不碰任何触发器（`trg_stage_no_skip` 只管 seq 连续性）。
  **这是决策，不是遗漏** —— 台账里写清楚，免得下一轮有人按计划原文把它改回 `active`。
- **DoD 缺口（本轮补齐）**：计划 TASK-6.1 的 DoD 要求签批后「自动走完 `release` 并**生成 zip 包**」，
  但全仓此前**没有任何 zip 生成器**（`zipfile` 只出现在 `app/docs.py` 的读取侧），
  `release` 阶段仅把交付物拷进 `release/` 目录 —— 这条 DoD 原本无条件不达成。
  已在 release 阶段补 `release/Bundle.zip`（`zipfile.ZipFile(..., ZIP_DEFLATED)`，不走 `_write`，
  因此不新增 deliverable 行、不改变既有计数），并由端到端用例断言（存在、`testzip()` 无损坏、
  条目 ≥3、含 `.md`、大小 >200B）。**写台账时要说「zip 产出能力本轮补齐」，不能说「原本就有」。**
- **回归状态（2026-10-26 03:31，冻结树）**：全量 `pytest -q` = **626 passed / 0 failed**
  （Lead 实测 `626 passed, 1 warning in 326.05s`，exit 0；gaps-doc 在冻结树上独立复跑
  `626 passed, 1 warning in 326.33s`，exit 0，两次一致）。626 = 622 基线
  + `tests/test_v54_l1_human_gate_strict.py` 新增 4 条 —— 后来补的 `state='pending'` / `raise`
  两处收口是加强既有 4 条用例的断言，不新增用例，所以总数仍是 626 而不是 627。
  收尾过程中先后有 2 条与 8 条存量用例按 v5.4 语义重写，其中两条（`test_m0_11_full_flow_reaches_completed`、
  `test_m6_10_human_criterion_can_be_signed_from_the_chat`）**反而显式 `delenv("M0_AUTO_SIGN_L1_FOR_TEST")`**
  —— 它们验证的就是「没人签批不许收尾」，全局自动签批一开，这两条等于什么都没验证。
  **「全绿」只对这次冻结树成立，不追认之前的中间态**（中途实测过 620 passed / 2 failed 与
  618 passed / 8 failed 两次中间态，根因都在 TASK-6.1 自身，不是文档或版本号改动）。

**如何避免重踩**：闸门的「通过」必须能一路追到证据（`evidence_snippet` + `checked_by` + 审计行）；
**不允许流程推进器代签**。测试要断言**负向事实**（「没证据时项目必停在 accept」），
而不只是「补了证据能过」—— 只测正向的测试，恰好是这次后门能活下来的原因。
凡是在代码里看到 `note='<阶段名>'` 这种自签备注，一律当红旗查。

## §75 `M0_PIPELINE_EXEC=0` 的测试捷径会污染验收指标（v5.4）

**现象**：`M0_PIPELINE_EXEC=0` 时 `execute_build()` 走 stub 分支：不调模型、不派 ACP，
直接写占位产物（`index.html` / `api_schema.json` / `*_delivery.md` / `*_stub.py`），
插一条 `runner='stub'`、`state='succeeded'` 的 run，然后把
`run_spawned → run_started → run_succeeded → review_passed` 全部推过去（`actor="stub"`），
`complete_wbs()` 收工（`app/pipeline.py:1231-1294`）。**整条验收链（L0/L1/L2 判据、红线扫描）
一步都没跑**。拿这样的库算 DoD 指标，会得到毫无意义的「完成率 1.00 / 零驳回」。

**物理根因**：这个短路本身是给离线单测和演练用的，默认值也没错
（`stub = os.environ.get("M0_PIPELINE_EXEC", "1") != "1"`，生产默认真执行）；
错的是**指标采集不看 run 的来源**。stub run 在数据里已经标了三处
（`task_runs.runner='stub'`、`artifacts.checksum='stub'`、状态机 actor=`stub`），
但项目/任务层面的完成率统计没有排除它们。

**修法（口径，不是代码缺陷）**：任何 DoD / 验收指标在使用前必须确认
**`M0_PIPELINE_EXEC=1`**；跨轮对比指标时，统计口径要显式排除 `task_runs.runner='stub'` 的 run。
当前会显式设 0 的地方：`tests/` 里绝大多数 UI/流水线用例、`scripts/e2e_commercial_dryrun.py`、
`tools/serve_ui_test.py`。`_flow_stage()` 另有 `M0_FLOW_EXEC` 开关，同理。

**如何避免重踩**：短路开关必须同时打上**可识别的标记**（这里已做：runner / checksum / actor 三处），
并在指标查询里显式过滤。报告里凡是出现「全绿 / 1.00 / 零驳回」，都要附带当时的环境变量快照
与 runner 分布 —— 否则你测的不是系统，是 stub。
