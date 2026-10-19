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
