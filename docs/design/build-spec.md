# 施工图（One-Shot Build Spec）

> 状态：**待你确认后立刻开工**｜原则：**一个工程一次建成，不分版本期**；但施工必须按依赖顺序（不然必返工）。
> 契约：`contracts/01-events.md`、`contracts/02-processes.md`、`contracts/03-projection-api.md`
> 功能面：`feature-parity.md`

---

## 1. 最终形态

```
                     你的 Windows 电脑
┌────────────────────────────────────────────────────────┐
│  AgentOS.exe（Tauri 2 + Rust core + React UI）          │
│  侧栏会话 │ 聊天 │ 详情/资料库/电脑                      │
│  本地桥（受控）：本机文件/命令/截图/剪贴板                │
│  语音输入 · 通知 · 托盘 · 自动更新 · 离线缓存              │
└───────────────────────┬────────────────────────────────┘
                        │ 出站 WSS（自签证书 + 公钥固定）
                        │ 无需域名、无需端口映射
┌───────────────────────▼────────────────────────────────┐
│  腾讯云 4C8G（193.112.172.223）                          │
│  nginx(TLS) → FastAPI 单进程（uvicorn）                  │
│  ├─ 事件流（SQLite WAL，单写者）                          │
│  ├─ 会话/Bot/群/交接/审批/预算/审计（复用现有）            │
│  ├─ Agent 内核（runner_native 扩展）                     │
│  ├─ 进程 supervisor                                     │
│  └─ 例行任务 cron                                       │
│                                                         │
│  Docker: computer（ubuntu24 + chromium + Xvfb + KasmVNC）│
│  └─ 每 Bot：一个 display + 一个 chrome profile            │
│  └─ 共享：/workspace、登录态同步服务                       │
└────────────────────────────────────────────────────────┘
        ▲ 企微（手机派活/审批/推送，复用现有 bridge）
```

**关键取舍（每一条都能说出理由）**

| 决定 | 选择 | 理由 | 被否方案 |
|---|---|---|---|
| 数据库 | **继续 SQLite（WAL）+ 单进程单写者** | 单用户、单服务器；并发写只来自一个进程；省 8–12 天迁移与 PG 运维。schema 保持 PG 兼容，将来要迁只换适配层 | Postgres：为多 worker 准备，但你现在不需要多 worker |
| Agent 内核 | **扩展现有 `runner_native`（Python 内工具循环）** | 不依赖 Linux 版 DSH CLI（未验证风险大）；工具全部通过 docker exec 在容器内执行；预算/事件/审批都能直接挂 | 容器内跑 DSH ACP：镜像 +800MB、启动慢、版本耦合、Linux 可用性未知 |
| 电脑形态 | **一个容器 + 每 Bot 一个 display/profile** | 贴 Grok Bot"一台共享电脑 + 每 Bot 一块屏"；4C8G 只养得起一个容器 | 每 Bot 一容器：内存/镜像/启动开销 ×N |
| 屏幕预览 | **服务端截图循环 → JPEG over WSS（1–2fps 自适应）** | 5Mbps 下 VNC 全时推流必卡；预览只要"看得见" | 全时 VNC：带宽不够 |
| 接管 | **KasmVNC 按需**（点"接管"才连） | 只在验证码/2FA 时用，带宽可控 | noVNC + 自研输入映射：多花 1 周 |
| 证书 | **自签 + SPKI 固定（Rust 侧校验）** | 不用买域名；WebView 不直接联网，绕开自签限制 | 域名 + Let's Encrypt：需要域名与备案风险 |
| 桌面端 | **Tauri 2 + Rust core + React 18** | devDeps 已有 Tauri CLI；包体小；证书/WS/本地桥都在 Rust 做 | Electron：+100MB，但生态稳；如果 Tauri 踩到硬坑，2 天内可换 |
| 语音 | **腾讯云 ASR + edge-tts** | 你已有腾讯云；实时通话不做 | 本地 whisper.cpp：4C 能跑但占 CPU |

---

## 2. 服务器现状与约束（按你截图实测）

| 项 | 实测 | 对施工的影响 |
|---|---|---|
| 规格 | 4 核 / 8GB / SSD 60GB | 容器限 1.5 核 / 1.5GB；FastAPI + 浏览器共存没问题 |
| 磁盘 | **已用 54.4%（32.7/60GB）** | 只剩 ~27GB。镜像必须瘦身（目标 ≤1.8GB），`/workspace` 配额 20GB，产物保留 90 天 |
| 带宽 | **5Mbps 峰值**（约 625KB/s） | 屏幕预览封顶 1–2fps/720p；接管时才开 VNC；禁止在容器里看视频 |
| 流量 | 500GB/月 | 预览按 45 分钟/天算约 200GB/月；服务端统计，80% 时自动降级并告警 |
| 公网 | **IPv4 193.112.172.223** | 可以直接连；防火墙只开 443（+22 限源 IP） |
| 系统盘 | 未知（截图未给发行版） | W0 第一步确认；Ubuntu 22/24 优先 |

**上线前必做**
1. 清盘：找出 32.7GB 里是什么（日志/旧镜像/快照），目标腾到 ≥35GB 可用
2. 装 Docker + compose 插件；配置 daemon `data-root` 到数据盘（如有）
3. nginx 反代 443 + 自签证书（IP SAN），HTTP 301 跳转
4. 腾讯云防火墙：只放 443 对全网、22 对你的出口 IP
5. 每日备份：SQLite `VACUUM INTO` + 卷快照，保留 14 天

---

## 3. 工作包（按依赖顺序施工，不是分版本交付）

> 每个包完成即可运行/可验证；我不会停下来等你验收才继续，除非遇到需要你决策的岔路。

### W0 · 底座（0.5 天）
- 服务器环境确认、Docker、nginx、证书、防火墙、目录规划（`/opt/agent-os/{data,workspace,logs,backup}`）
- 现有代码从 Windows 迁到 Linux 的**路径修复**（工作区、run 日志、DSH CLI 三处写死）
- 完成判据：`curl -k https://127.0.0.1/v1/health` 返回 200；`docker run hello-world` 成功

### W1 · 数据层与事件原语（1 天）
- 新表：`events / sessions / session_snapshots / bots / processes / devices / uploads / routines / routine_runs / skills / memories_v2 / artifacts_v2`
- `emit()` 唯一写入口 + kind 闭集校验 + 8KB 降级 + `client_msg_id` 幂等
- 迁移脚本（SQLite `ALTER TABLE` 幂等模式，沿用现有 `_ensure_column` 风格）
- 完成判据：单测覆盖"重复 client_msg_id 只落一条"、"payload 超限转 artifact"、"游标单调"

### W2 · 事件流服务（1 天）
- 内存广播器 + WSS `/v1/stream`（backlog→实时同连接切换、ack/裁剪、20s 心跳）
- 快照 API、补拉 API、多设备订阅、权限过滤
- turn 折叠任务（每天）
- 完成判据：脚本强制断连 100 次，事件不丢不重；两台设备同时在线互相不干扰

### W3 · 容器电脑（1.5 天）
- 镜像：ubuntu24 + chromium + Xvfb + fluxbox + KasmVNC + python3.12 + node20 + git + 常用 CLI（**目标 ≤1.8GB**）
- 生命周期：wake / sleep / reset（保留卷）、健康探针、冷启动 ≤8s
- 每 Bot：display + chrome profile 目录；共享 `/workspace` 与 cookie 同步服务
- 截图循环：x11grab → 缩放 → JPEG → WSS（自适应 fps/质量）
- 完成判据：`wake` 后 8s 内出第一帧；`sleep` 后再 `wake` 文件仍在；重启服务端不丢电脑

### W4 · 进程 supervisor（1 天）
- `setsid` 脱离、日志重定向/轮转、`proc_*` 五工具、15s tick、`max_runtime`、杀进程组
- 重启对账：adopt / orphaned / 恢复建议
- 完成判据：杀掉服务端后进程仍活；重启后正确 adopt；跑 2 小时任务模型调用 ≤8 次

### W5 · Agent 内核与工具（2 天）
- 扩展 `runner_native`：流式、工具循环、上下文压缩（保留用户原话 + 摘要，抄 Codex 思路）、预算闸、取消
- 工具集：`exec` / `proc_*` / `fs_*` / `browse_*`（CDP）/ `web_search` / `ask_human` / `approval_request` / `memory_*` / `local_*`（桥）
- 输出规范：事实/推断/已完成/待批准/未决 五段式；证据引用
- 完成判据：一条"看网页并改文件"的真实任务端到端跑通，事件流完整

### W6 · Bot / 会话 / 群 / 交接（1.5 天）
- bots/sessions/groups、主 Bot 引导、@ 路由、群 admin、每成员独立 session、群记忆隔离
- handoff 三事件 + hop/预算/环路四道闸 + 防循环
- 完成判据：一句话让 3 个 Bot 协作完成一个任务，DAG 可回放，环路上限生效

### W7 · 例行任务 / 技能 / 资料库（1.5 天）
- routines（cron + webhook 触发、试跑、暂停、历史、50 上限、5 分钟最小间隔、长期不活跃自动暂停）
- skills（从 turn 事件流生成草稿、`/` 引用、版本）
- library（文件树 + 记忆 + 技能；SQLite FTS5 搜索）
- 完成判据：教一次 → 存技能 → 定时跑 → 错过补偿 → 失败告警

### W8 · 投影 API + exe（3 天，最大一块）
- 服务端：契约 03 的全部端点 + 本地桥路由 + 语音接口 + 审批/问题/机密值
- exe Rust core：TLS 固定、WSS 客户端、断线重连、离线缓存、本地桥服务、通知、托盘、全局快捷键、自动更新、语音采集/播放
- exe React UI：三栏布局、聊天流（工具过程折叠/状态条/卡片）、详情页、资料库页、电脑页（进程/文件/截图/接管）、审批中心、搜索/快捷键
- 完成判据：**拔网线 60s 再插回**，界面自动恢复并补全事件；本机工具调用会弹批准框；自动更新能装上下一个版本

### W9 · 手机与收尾（1 天）
- PWA 响应式（聊天/审批/看进度）+ 企微卡片升级（完成/需签字/失败）
- 用量页、健康检查、日志、备份演练
- 端到端验收脚本（见第 6 节）
- 完成判据：手机上能派活、看进度、签字；全部验收清单通过

**合计约 13–15 个工程日**（我一个人连续推进；不含你确认/提供凭据的等待时间）。
代码量估算：服务端 +2500~3500 行、容器/脚本 +400、Rust +1500~2000、React +3000~4000。

---

## 4. 与现有代码的关系

| 现有资产 | 处理 | 说明 |
|---|---|---|
| `app/engine/runner_native.py` | **改造为主内核** | 加流式、工具注册、压缩、取消、事件上报 |
| `app/state_machine.py`、`models.py` | 保留 | 任务状态继续用，外层套 job |
| `app/finance.py`、`finops.py`（预算） | 保留 | 接 reserve/settle + 原子更新 |
| `app/acceptance/` | 保留 | 技能/例行任务的验收判据 |
| `app/rbac.py`、`audit.py`、`tenancy.py` | 保留 | 工具级白名单基于它扩展 |
| `app/idempotency.py`、`locks.py` | 保留 | 事件幂等 + 租约 |
| `app/wecom/*`（审批/卡片/签名页） | 保留 | 手机端主力 |
| `app/octo/*`（Octo 桥） | **冻结** | 不再演进；消息入口统一到新 sessions |
| `app/ui/*`（现有 Web 工作台） | 保留为管理台 | 审批/项目/组织/审计留在 Web |
| `app/engine/lowil.py`、`tools/sandbox_win.py` | **停用** | Linux 上隔离由容器承担；保留给本地开发 |
| `app/engine/dsh_adapter.py` | 保留但降级 | 本地开发/对比用；云端不用 |
| `web/`（现有 React） | 保留 + 抽出设计 token | exe 的新 UI 复用风格，不直接复用页面 |
| `docs/runs/*.jsonl` | 保留 | 技能生成的原料 |

---

## 5. 新增目录建议

```
app/
  events.py            # emit() + 闭集 + 幂等
  stream.py            # 广播器 + WSS
  computer/
    docker_driver.py   # 容器生命周期
    screen.py          # 截图循环 + 自适应
    browser.py         # CDP 工具
  processes.py         # supervisor + proc_* 工具
  agent/
    loop.py            # 内核（runner_native 演进）
    tools.py           # 工具注册表
    compress.py        # 上下文压缩/checkpoint
  sessions.py          # Bot/会话/群/路由/handoff
  routines.py          # cron + webhook
  skills.py            # 技能
  library.py           # 资料库 + FTS
  api_v1/
    bootstrap.py stream.py messages.py bots.py computer.py processes.py
    routines.py skills.py approvals.py devices.py voice.py
  device_bridge.py     # 本地桥路由 + 策略
desktop/               # Tauri 2 工程
  src-tauri/           # Rust core
  src/                 # React UI
deploy/
  Dockerfile.computer
  docker-compose.yml
  nginx.conf
  certs/
docs/design/contracts/ # 三份契约（已完成）
```

---

## 6. 端到端验收清单（全部通过才算建成）

1. `AgentOS.exe` 首次启动 → 配对 → 看到 Bot 列表，<5s
2. 发一句话 → 云端 Bot 开始干 → 聊天里出现工具过程 → 状态条显示进度
3. 派一个 30 分钟任务 → 关闭 exe → 20 分钟后重开 → 事件补全、结果显示
4. 同样任务 → `systemctl restart agent-os` → 任务不丢、进程不丢、事件补全
5. 打开"电脑"页 → 看到实时截图、进程列表、日志尾；切走页面 30s 后推流暂停
6. 遇到验证码 → 桌面/企微同时收到 needs_human → 点接管 → 在 VNC 里输入 → 交还 → 任务继续
7. 群聊："@前端 做个落地页，@文案 配套文案，完成后主 Bot 汇总" → 三 Bot 协作 → DAG 可回放
8. "把刚才的流程存成技能，每周一 9 点跑" → 生成技能 → 建例行任务 → 试跑成功 → 在详情页看到下次运行时间
9. 让 Bot 读本机 `D:\报告\Q3.xlsx` → exe 弹批准 → 批准后读取成功 → 拒绝后 Bot 收到明确错误
10. 手机企微：说一句话派活 → 完成卡 → 点签字 → Web 端看到审批结果
11. 拔网线 60s：界面显示"连接已断开（最后进度 43%）"，恢复后自动补全
12. 自动更新：发布 1.0.1 → 旧客户端提示并静默升级成功

---

## 7. 风险与回滚

| 风险 | 概率 | 影响 | 预案 |
|---|---|---|---|
| Tauri 自签证书/证书固定踩坑 | 中 | 高 | W0 先做 30 分钟 spike；不行就退到 `http://` + WireGuard/Tailscale 隧道 |
| 容器内 Chromium 截图黑屏/花屏 | 中高 | 中 | 用 `x11grab` 而非 `scrot`；失败降级到 CDP 页面截图（只拍浏览器） |
| 4C8G 跑容器 + 浏览器 + 服务端吃紧 | 中 | 中 | 容器限 1.5C/1.5G；关闭容器内多余服务；高峰期串行执行 |
| 5Mbps 下预览体验差 | 中 | 低 | 自适应 + 默认 1fps + 只在电脑页可见时推 |
| 磁盘 27GB 不够 | 中 | 高 | 清盘、镜像瘦身、workspace 配额、产物 90 天清理 |
| 「一步做完」的范围失控 | 高 | 高 | 以 `feature-parity.md` 为唯一范围基线；任何新增先改表再动手 |
| 迁移破坏现有系统 | 中 | 高 | 不动现有 `/tasks` 与 Web 管理台；新代码走 `/v1`；旧路径回滚开关保留 |

**回滚**：所有新代码加 `MODE=v1|legacy` 开关；旧系统继续跑；v1 出问题切回 legacy 不影响现有任务。

---

## 8. 我需要你提供的（开工前置）

| 需要 | 用途 | 不给的替代 |
|---|---|---|
| **服务器 SSH 登录方式**（密码或密钥，或你自己执行我给的命令） | W0 全部环境搭建 | 你按我给的脚本逐条执行并回贴输出（会慢 3–5 倍） |
| **腾讯云防火墙开放 443**（22 限你的 IP） | exe 连接 | 用其他端口 + nginx，仍需放行 |
| **ASR/TTS 的 key**（腾讯云语音，或确认用本地 whisper + edge-tts） | 语音输入 | 先用"文字输入"，语音发布时补 |
| 确认可清盘/可装 Docker | 腾出 27GB | 不装 Docker 则电脑只做"文件+命令"，没有浏览器/截图 |
| 服务器发行版（`cat /etc/os-release`） | 镜像与 systemd | 我按 Ubuntu 24 写，其他发行版需微调 |

---

## 9. 我开工的第一行代码

按依赖顺序，第一段会同时落三样（都在 W1/W2）：
1. `app/events.py` — `emit()` + 闭集 + 幂等
2. `app/schema_v1.sql` — 新表 DDL（SQLite 写法，PG 兼容）
3. `tests/test_events_contract.py` — 把契约 01 的检查单变成可执行测试

写完后立刻能验证"事件流是唯一真相"这条宪法，后面所有模块都建在它上面。
