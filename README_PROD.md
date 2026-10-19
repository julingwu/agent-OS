# README_PROD · 生产运行手册

MVP 已结项。这份文档只讲**怎么把这套系统跑起来、在哪看、出问题怎么查**，不再讲设计。

---

## 0. 一键启动（重启电脑后的唯一动作）

双击：

```
C:\oaagent-m0\scripts\start_all.cmd
```

它按顺序做四件事，每一步都先检查再动手，**重复执行安全**：

| 步骤 | 动作 | 失败时 |
|---|---|---|
| 1 | 确保 Docker Desktop 在跑，`docker compose up -d` 拉起 Octo 十个容器 | 打日志，Octo 跳过，底座照常起 |
| 2 | 若 8077 没监听，启动 FastAPI（含 Octo 桥接 + 审批轮询） | 提示看 `data\logs\oaagent-8077.err.log` |
| 3 | 若没有 cloudflared 进程，起一条 quick tunnel，并把新域名写回 `.env` | 只影响「签名链接」备用通道 |
| 4 | 打一张健康看板：底座 / 监控页 / Octo / 审批轮询 | — |

只想看状态、不启动任何东西：

```
powershell -NoProfile -ExecutionPolicy Bypass -File C:\oaagent-m0\scripts\start_all.ps1 -Status
```

跳过某些步骤：

```
... -SkipOcto        # 不碰 Docker（只跑底座+桥接）
... -SkipTunnel      # 不起隧道（原生审批不需要它）
```

---

## 1. 开机自启（装一次，之后不用管）

`scripts\start_all.cmd` 的快捷方式已放进启动文件夹：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\oaagent-start.cmd
```

登录 Windows 后会自动执行一次上面的四步。若想临时取消自启，删掉那个文件即可。

---

## 2. 现在在跑什么

| 组件 | 端口 / 地址 | 说明 |
|---|---|---|
| FastAPI 底座 | http://127.0.0.1:8077 | 任务、判据、预算、沙箱、审计、企微、Octo 桥接 |
| 健康检查 | http://127.0.0.1:8077/health | `{"ok":true,"service":"oaagent-m0"}` |
| 运行看板 | http://127.0.0.1:8077/octo/watch?token=`<OCTO_SIM_TOKEN>` | 每 3 秒刷新：runs / tokens / 预算 / 回复链 |
| 董事长工作台 | http://127.0.0.1:8077/dashboard.html | 人工签字兜底入口 |
| Octo 网页 | http://127.0.0.1:28080 | 组织架构 + 群聊（董事长账号 `008613800000001`） |
| Octo API | http://127.0.0.1:28080/v1 | Bot/群/消息接口 |
| MySQL | 127.0.0.1:23306 | Octo 库（`octo`），桥接层尾随 `message*` 分表 |
| 隧道（可选） | `https://<随机>.trycloudflare.com` | 只服务签名链接；域名每次重启会变 |

凭据都在本地：

```
C:\oaagent-m0\.env                     # 全部密钥（DEEPSEEK / 企微 / Octo / MySQL）
C:\oaagent-m0\vendor\octo_admin_pwd.txt # Octo superAdmin
C:\oaagent-m0\vendor\octo_db_pwds.txt   # Octo 各库密码
```

---

## 3. 常驻的两条通道

**① 企微原生审批（主通道，不依赖公网）**

- 轮询线程：`WECOM_APPROVAL_POLLER=1`，30 秒一次（`WECOM_APPROVAL_TICK`）
- 行为：扫描 `pending_review` 任务里待人工的判据 → 自动 `oa/applyevent` 建审批单 → 你在「审批」应用点同意 → 轮询到 `sp_status=2` → 写 `checked_by/note` → 重跑验收 → 任务 `completed`
- 模板：**AI任务人工验收**，`id=<approval_template_id>`，控件 `Text-01`（任务ID）/ `Text-02`（验收结论）
- 查看：`GET /wecom/approval/list`（带 `X-Wecom-Token`）

**② Octo 群聊桥接**

- `OCTO_RUNNER=1`：尾随 MySQL 消息表，群里的中文任务 → 建任务 → 真模型执行 → 回帖
- 防死循环：`OCTO_MAX_BOT_CHAIN=3` / `OCTO_CHAIN_WINDOW=600` / 每 Bot 预算 200 次
- `OCTO_RUNNER_ENGINE=acp`（真模型）、`OCTO_TASK_BUDGET=80000`（单任务预算闸）

Web 工作台、签名链接两条兜底都保留，任一条挂了不影响另外两条。

---

## 4. 手工命令（一键脚本失效时用）

```powershell
# Docker / Octo
$env:PATH += ";C:\Program Files\Docker\Docker\resources\bin"
cd C:\oaagent-m0\vendor\octo-deployment\docker
docker compose up -d
docker ps --format "{{.Names}} | {{.Status}}"

# 底座
cd C:\oaagent-m0
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8077 --log-level info

# 隧道（可选）
& "$env:USERPROFILE\bin\cloudflared.exe" tunnel --url http://127.0.0.1:8077 --no-autoupdate
```

停止：

```powershell
# 只停底座
Get-NetTCPConnection -State Listen -LocalPort 8077 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
# 停 Octo
cd C:\oaagent-m0\vendor\octo-deployment\docker; docker compose down
```

---

## 5. 排障对照表（都是真机踩过的）

| 现象 | 原因 | 处理 |
|---|---|---|
| 签字按钮点了没反应 | quick tunnel 域名变了 / 进程死了（CF 1033） | 用原生审批（不需要回调）；或 `-SkipTunnel:$false` 重起隧道并在企微后台更新 URL |
| 审批一直不触发签字 | 轮询查详情用 GET 会 301025 | 代码已改 POST body；查 `/wecom/approval/list` 的 `poller.last` |
| `48002 api forbidden` | 自建应用不在「审批 → 可调用接口的应用」 | 企微后台加应用 <自建应用> |
| `controlId_format_err` | 控件 id 写成 `task_id` | 必须是 `Text-01` / `Text-02` 这种格式 |
| 群里机器人不回 | `OCTO_RUNNER` 没开 / 消息表游标跳过 | 看 `/octo/watch` 的 runner 与 handled/dropped |
| 任务一直 running | ACP 单轮耗 token 大，预算闸到轮末才生效 | 看 `data\logs\oaagent-8077.log`，必要时调 `OCTO_TASK_BUDGET` |
| Octo 打不开 | 容器没起 / MinIO 挂了 | `docker ps` → `docker compose up -d` |
| 端口 8077 被占 | 旧进程没退 | `Get-NetTCPConnection -LocalPort 8077` 后 kill |
| 所有 run 都 `timeout waiting for initialize` | **DSH 桌面端开着**，它占住 agent runtime 的单实例锁 | 关掉「DeepSeek Harness」桌面进程；`start_all.ps1` 现在启动前会自动关（`-KeepDshDesktop` 可跳过） |
| 启动时弹 `icacls.exe` 的 UAC | 工作目录在仓库里（ACL 属 Administrators），打 Low 标签需要 WRITE_OWNER | 工作区已迁到 `%LOCALAPPDATA%/oaagent-m0/workspace`（用户自己所有），静默打标签；代码里已删除任何 `-Verb RunAs` 兜底 |

---

## 6. 每天要看的三个数

1. `/octo/watch` 顶部：runner 是否 running、engine 是否 `acp`、budget/run
2. `/reports/usage`：按业务包统计的 tokens 与成功率（真实花费）
3. `/wecom/approval/list`：`poller.last` 里 `opened/approved/rejected/errors`

---

## 7. 已知边界（别当成 bug）

- ACP 预算闸粒度是「一轮」，超支上限 = 一轮成本；cap 设太小会把任务砍在半路
- 字数这类指标靠**任务标题**里的数字，retry prompt 里写数字不生效（§47）
- 沙箱是 Windows Low 完整性级别，挡误写上级目录，不是虚拟机级隔离
- Octo 开源版没有 Agent 运行时，群里说话的是桥接层，不是 Octo 自带 bot
- quick tunnel 域名每次重启都变；原生审批不依赖它，所以可以忽略

---

## 8. 自研控制台（Agent OS Console）

Octo 已弃用，代码封存在 `vendor/` 下不再启动。新的前端在本仓库里：

```
web/             React 18 + Vite（自写 CSS，无 UI 框架、无外部 SaaS）
web/dist/       构建产物，由 FastAPI 挂在 /app
app/ui/         BFF：会话 Cookie 登录 + 组织/会话/任务接口
```

**访问**：`http://127.0.0.1:8077/app/` — 用身份 ID + HMAC 密钥登录（浏览器不保存密钥，
登录后只持 HttpOnly 会话 Cookie，所有签名在后端完成）。

**改前端**：

```powershell
cd C:\oaagent-m0\web
npm install            # 首次
npx vite build         # 产物直接落到 dist，FastAPI 无需重启
```

开发态热更新：`npx vite`（5173，已配 `/ui` 代理到 8077）。

**接口速查**（全部 `/ui` 前缀，除 login 外都要会话 Cookie）：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | /ui/login | {identity_id, secret} → 会话 Cookie |
| GET | /ui/me · /ui/org · /ui/chats | 当前身份 / 组织树 / 会话列表 |
| POST | /ui/members | 新增 AI 员工（建职位+身份+DM） |
| POST | /ui/chats/{id}/messages | 发一句话 → 建任务并后台执行 |
| GET | /ui/chats/{id}/messages?after= | 增量拉消息 |
| GET | /ui/tasks/{id} | 任务 + 判据 + 运行 + 思考链 |
| POST | /ui/tasks/{id}/action | accept / reject / abort |

**白盒化**：ACP 的 `agent_thought_chunk` 与 `tool_call` 被写入 `run_stream` 表，
前端轮询渲染成「💭 思考与工具」折叠卡；任务完成渲染验收卡（判据逐条 + 三个按钮）。

**已知限制**：内容类任务的「字数」判据经常不通过 —— 模型输出稳定比目标少 15–25%
（见 DESIGN_GAPS §47），这是模型能力问题，不是 UI 问题；卡片会如实标红。

### 8.1 群聊（M10）

`会话` Tab 顶部 **+ 创建群聊** → 勾选成员（第一个是 PM，最后一个是复核人）。
群里的任务流：PM 拆解并 @ 被指派人 → 被指派人「收到，马上开始」→ 执行 → 验收卡 → 复核人收尾。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /ui/employees | 成员候选（含职位、模型） |
| POST | /ui/groups | {name, employee_ids[]} → 建群 |

**防死循环**：每群 10 分钟最多 4 条 agent 发言（`group_hops`），超了静默。演示前若刚跑过，
先 `delete from group_hops where chat_id='...'` 重置窗口。

### 8.2 自动扩写修复（M10）

字数判据不过时不再直接 rejected：

```
run → 验收 → 字数不足 → 同 session 补写（最多 2 次）→ 再验收 → 仍不过才交人工
```

审计里能看到 `repair_attempt` / `repair_result` 两条事件，含原字数、目标字数和修复后字数。
预算 `REPAIR_BUDGET`（默认 30k），并按实体池剩余额度自动收敛。

---

## 9. 桌面版 V1.0（Tauri）

```
web/src-tauri/            Rust 壳（tauri 2.12）
web/src-tauri/target/release/oaagent-console.exe   ← 双击即用，3.0 MB
```

**构建**（改完前端先 `npx vite build`，再打包）：

```powershell
cd C:\oaagent-m0\web
npx vite build
npx tauri build          # 需要 MSVC Build Tools + rust stable-msvc
```

**运行前置**：本机 `127.0.0.1:8077` 底座必须在跑（`scripts\start_all.ps1 -SkipOcto -SkipTunnel`）。
exe 里没有后端，它只连本地 8077；后端没起会卡在登录页。

**与浏览器版的差别**：桌面壳的页面跑在 `tauri://localhost`，跨站 Cookie 不成立，
所以登录后把会话票据存进 `localStorage`，之后每个请求带 `Authorization: Bearer`；
后端 `_token` 依赖同时接受 Cookie 和 Bearer，CORS 只放行 tauri 来源。

**已知限制**：
- 打包时 `拒绝访问 (os error 5)` 有两个真实来源，跟权限无关：
  ① exe 正在运行 → cargo 删不掉旧文件；② `%LOCALAPPDATA%	auri
sis-3.13` 是半截解压残留。
  先 `Stop-Process oaagent-console`，再 `rm -rf "$LOCALAPPDATA/tauri/nsis-3.13"`，重跑 `npx tauri build` 即可。
- exe 不负责拉起后端；要「双击即全绿」得把 `start_all.ps1` 塞进壳里当 sidecar（未做）。

## 10. 开通一条业务线（模型池 + 预算池）

一条业务线 = **一个法人主体（legal_entity）+ 一个 token 预算池 + 若干员工（各自带模型）**。
主体之间数据隔离、预算隔离，互不挪用。

### 10.1 一键开通

```powershell
.venv\Scripts\python.exe scripts\new_business_line.py `
  --id LE-AD --name 广告事业部 --pool 200000 --model deepseek-chat `
  --member "小广:文案:pack=content-v1" `
  --member "小图:设计:model=deepseek-chat"
```

幂等：主体、池子、员工已存在就跳过；`--reset-pool` 才会覆盖池额度。
输出会打印每个员工的 `AI-xxxx` 编号和池子剩余额度。

### 10.2 池子的语义（别混）

| 概念 | 存在哪 | 含义 |
|---|---|---|
| **业务线预算池** | `budgets` 里 `task_id IS NULL AND identity_id IS NULL` 的那一行 | 这条线**总共**能用多少 token |
| **单任务额度** | 调度时自动建 `budgets` 行（task_id + identity_id） | 一次执行最多烧多少，不会被别的 agent 抢走 |
| **闸门** | `entity_pool()` + `budget_limit()` | 单任务额度取「任务默认」与「池剩余」的较小值；池见底则拒绝 spawn |

池子用尽后新任务直接 `failed`（`budget denied before spawn`），不会偷偷超支。

### 10.3 模型池（每个员工一个模型）

- 员工级：`ai_employees.model`，控制台【组织架构 → 点员工 → 模型】可改，等价于
  `PATCH /ui/employees/{id} {"model": "deepseek-reasoner"}`。
- 全局：设置面板里的 API Key 是**兜底凭据**；员工模型名决定走哪条 provider 路由。
- 建议：文案用 `deepseek-chat`（便宜），架构/攻坚用 `deepseek-reasoner`（慢但稳）。

### 10.4 查看余额

```sql
-- 每条业务线还剩多少
SELECT e.id, e.name, b.limit_tokens, b.used_tokens,
       b.limit_tokens - b.used_tokens AS left_tokens
FROM budgets b JOIN legal_entities e ON e.id = b.legal_entity_id
WHERE b.task_id IS NULL AND b.identity_id IS NULL;
```

控制台【文件库】看产物，【运行详情】看单任务 token 消耗与判据进度。

### 10.5 交付包（pack）选择

| pack | 判据重在哪 | 用在哪 |
|---|---|---|
| `content-v1` | 字数区间、关键词、广告法禁用词、调性 | 文案、文章、投放素材 |
| `code-v1` | 文件存在、语法、**必须发网络请求**、可读性 | 抓数、接口、自动化 |
| `code-local-v1` | 文件存在、语法、可读性（**不要求联网**） | 本地脚本、工具、算法题 |
| `review-v1` / `finance-v1` / `web-v1` | 评审结论 / 财务字段 / 页面与文案 | 对应场景 |

代码类任务里没有「抓取/接口/http」等词时，自动落 `code-local-v1`——否则 FizzBuzz
这种本地脚本会被「必须联网」判据误杀（真机踩过）。
