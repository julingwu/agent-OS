# 契约 03 · 投影 API（Projection API v1）

> 状态：**定稿待实现**｜消费者：exe（Tauri）、PWA、企微推送、CLI
> 一句话：**exe 只做三件事——渲染投影、发送意图、订阅事件。** 它不保存真相，不跑 agent。

---

## 1. 通信拓扑（为什么 exe 不直连服务端）

```
┌──────────────── exe (Tauri 2, Windows) ────────────────┐
│  React UI ──(Tauri IPC)──▶ Rust core                   │
│                            ├─ HTTPS/WSS 客户端（证书 SPKI 固定）│
│                            ├─ 本地桥 127.0.0.1:<随机端口>（受控）│
│                            ├─ 本地 SQLite（事件缓存 + 发件箱）│
│                            ├─ 通知/托盘/全局快捷键/自动更新    │
│                            └─ 语音采集/播放                  │
└──────────────────────────┬─────────────────────────────┘
                           │ 出站 WSS（exe 主动连，服务器无入站）
┌──────────────────────────▼─────────────────────────────┐
│  服务端（Linux, FastAPI 单进程）                          │
│  投影 API · 事件流 · 会话/群 · 审批 · 例行任务 · 容器/进程   │
└────────────────────────────────────────────────────────┘
```

**三个关键决定**
1. **React 不直接连网络**，所有请求/事件/屏幕帧都过 Rust。好处：自签证书 SPKI 固定、token 不进 WebView、断线重连和离线缓存都在 Rust 做、屏幕帧解码不卡 UI。
2. **exe 只出站**：不需要公网入站、不需要路由器端口映射、NAT 后面也能用。
3. **服务器地址 + 公钥指纹在配对时写入**，之后固定校验；换服务器要重新配对。

---

## 2. 配对与鉴权

```
1. 管理台生成配对码（6位数字，5分钟有效，一次性）
2. exe 首次启动输入：服务器地址 + 配对码
3. exe 校验证书指纹（用户可看到指纹前 8 位，确认后记住）
4. POST /v1/devices/pair {code, device:{name, kind, caps}} → {device_id, device_token}
5. 之后所有请求：Authorization: Bearer <device_token>
6. 长连接：WSS /v1/stream?ticket=<一次性票据>&since=<cursor>
```

- `device_token` 存 Rust 侧系统凭据库（Windows Credential Manager），**不进前端、不进日志**。
- 管理台可看设备列表、踢下线、重置配对。

---

## 3. REST 端点（只列 v1 必须的）

### 3.1 启动与同步
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/bootstrap` | `{bots[], sessions[], server:{version, caps, limits}, device}` — 首屏 |
| GET | `/v1/sessions/{sid}/snapshot?limit=50&before_id=` | `{events[], cursor, has_more}` |
| GET | `/v1/events?since=&kinds=&limit=200` | 补拉（WSS 不可用时的降级） |

### 3.2 Bot 与会话
| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/v1/bots` | 列表 / 创建 `{name, role, persona?, model?}` |
| GET/PATCH/DELETE | `/v1/bots/{bid}` | 详情（含 tags、model、usage）/ 改名换头像 / 删除 |
| GET | `/v1/bots/{bid}/details` | 详情页聚合：人格摘要、标签、模型、用量、例行任务数、技能数 |
| POST | `/v1/bots/{bid}/duplicate` | 复制（不含历史/记忆） |
| GET/POST | `/v1/sessions` | 列表 / 创建（单聊或群，`{bot_ids[], title?}`） |
| POST | `/v1/sessions/{sid}/messages` | 发消息 `{text, attachments[], client_msg_id, reply_to?}` → 202 |
| POST | `/v1/sessions/{sid}/stop` | "停"：中断当前 turn（不撤销已完成动作） |
| POST | `/v1/sessions/{sid}/handoffs` | 手动触发交接 `{from, to, task}` |
| GET/POST | `/v1/sessions/{sid}/reactions` | 表情 |
| GET | `/v1/search?q=&kinds=message,file,skill,routine` | 全局搜索（FTS5） |

### 3.3 电脑
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/bots/{bid}/computer` | `{status, display, screen:{w,h,fps}, uptime, processes[], workspace:{used,total}}` |
| POST | `/v1/bots/{bid}/computer/wake` | 冷启动容器（幂等） |
| POST | `/v1/bots/{bid}/computer/sleep` | 停止容器（保留卷） |
| POST | `/v1/bots/{bid}/computer/reset` | 从快照重建（保留 /workspace 与登录态） |
| GET | `/v1/bots/{bid}/processes` | 进程列表 |
| GET | `/v1/processes/{pid}/log?offset=&lines=80` | 日志尾（流式接口见 WS） |
| POST | `/v1/processes/{pid}/kill` | 杀进程组 |
| POST | `/v1/processes/{pid}/rerun` | 按原命令重跑（orphaned 用） |

### 3.4 资料库 / 技能 / 例行任务
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/bots/{bid}/library?type=file,memory,skill&q=` | 资料库聚合 |
| GET | `/v1/artifacts/{aid}` / `?download=1` | 预览/下载（短期票据） |
| POST | `/v1/uploads` | 分片上传（≤200MB）→ `{upload_id}`，再 attach 到消息 |
| GET/POST | `/v1/routines` | 列表 / 创建 `{bot_id, name, schedule{cron,tz}|trigger, skill_id?, instruction, approval_boundary}` |
| POST | `/v1/routines/{rid}/test` | 试跑（真实执行，带确认） |
| PATCH | `/v1/routines/{rid}` | 暂停/恢复/改期 |
| GET | `/v1/routines/{rid}/runs` | 最近 20 次运行记录 |
| GET/POST | `/v1/skills` | 列表 / 从事件流生成草稿 `{source_turn_id}` |
| POST | `/v1/skills/{sid}/teach` | 进入示范录制模式（浏览器动作录制，最长 10 分钟） |

### 3.5 审批 / 人机协同 / 语音
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/approvals?state=pending` | 待批列表 |
| POST | `/v1/approvals/{aid}` | `{decision: allow/deny/allow_always}` |
| GET | `/v1/questions?state=pending` | `needs_human` 待答 |
| POST | `/v1/questions/{qid}` | `{answer}` → 恢复 turn |
| POST | `/v1/secrets/{sid}` | 提交敏感值（不进事件/不进模型上下文） |
| POST | `/v1/voice/transcribe` | multipart 音频 → `{text}` |
| POST | `/v1/voice/speak` | `{text, voice?}` → 音频（语音备忘） |

---

## 4. WSS 协议（`/v1/stream`）

**帧格式**：JSON 文本帧；屏幕帧走独立二进制通道（第 5 节）。

```jsonc
// 服务端 → 客户端
{"t":"hello","server":{"version":"1.0.0","caps":["screen","local","voice"]},"cursor":1048577}
{"t":"events","replay":true,"items":[/* envelope[] */],"cursor":1048580}
{"t":"typing","session_id":"ses_1","state":"on","bot_id":"bot_1"}
{"t":"approval","approval_id":"apr_1","risk":"high","action":"发送邮件","options":["allow","deny"]}
{"t":"question","question_id":"q_1","text":"验证码是多少？","screenshot_ref":"art_9"}
{"t":"computer","bot_id":"bot_1","status":"awake","screen":{"w":1280,"h":800}}
{"t":"process","process_id":"proc_1","status":"running","elapsed_s":3720,"progress":0.43}
{"t":"device","device_id":"dev_1","online":true}
{"t":"error","code":"TURN_FAILED","message":"模型超时","retriable":true}

// 客户端 → 服务端
{"t":"ack","cursor":1048580}
{"t":"ping","ts":1761...}
{"t":"focus","session_id":"ses_1","visible":true}   // 用于暂停推送屏幕帧/通知去重
```

**规则**
- 一帧 ≤ 200 条事件；backlog > 500 条时先发 `{"t":"syncing","total":N}`，再分帧。
- 心跳 20s；45s 无 pong → 重连（指数退避 1s→30s，抖动 ±20%）。
- 重连参数 `since=<最后 ack 的 id>`；服务端从该 id 之后补发，**同一条连接内**完成 backlog→实时切换。

---

## 5. 屏幕流（"电脑"页预览）

**协议**：独立 WSS `/v1/screen/{bot_id}`，二进制帧。

```
客户端 → 服务端（JSON）:
  {"t":"subscribe","fps":2,"quality":60,"max_w":1280,"mode":"preview"}
  {"t":"ack_frame","seq":17,"recv_ms":40}      // 自适应用
  {"t":"unsubscribe"}

服务端 → 客户端:
  {"t":"meta","w":1280,"h":800,"fps":2,"quality":60}
  <binary: JPEG bytes>                        // 帧，带 seq 前缀（4字节）
```

**自适应策略（5Mbps 带宽下的关键）**
- 初始 `1280×800 @2fps q60` ≈ 100–200 KB/s（约 1–1.6 Mbps）
- 若 ack 延迟 > 1.5s / 丢帧 > 20% → 降 fps → 降 quality → 降分辨率
- 页面不可见（`{"t":"focus","visible":false}`）→ 服务端降到 0.2fps
- 连续 30s 无人看 → 暂停推流
- **每月流量预算 500GB**：按 1.5Mbps 持续看 ≈ 45 分钟/天 × 30 天 = 200GB，在预算内；超过 80% 服务端主动降级并提示

**接管（takeover）**
- 预览只是"看"。要"操作"→ `POST /v1/bots/{bid}/computer/takeover` 返回一次性 VNC 票据
- exe 内嵌 KasmVNC（`https://<host>/vnc/?ticket=...`），只在接管时用，退出即断
- 接管期间预览暂停（省带宽）
- 5Mbps 下 KasmVNC 可用（自适应 JPEG/WebP），但视频类操作会卡；纯表单/验证码没问题

---

## 6. 本地桥协议（exe 的能力暴露给云端 Bot）

**方向**：exe 出站建连 `WSS /v1/device`，服务器**下发**工具调用。

```jsonc
// exe → 服务端
{"t":"hello","device_id":"dev_1","caps":["exec","fs","open_app","clipboard","screenshot"],
 "policy":{"default":"ask","allow":["fs.read:/Users/me/Documents"],"deny":["exec:rm"]}}

// 服务端 → exe（Bot 请求操作本机）
{"t":"tool_call","call_id":"c_1","tool":"exec","args":{"cmd":"ls ~/Downloads"},"approval":"ask","reason":"Bot 想查看下载目录"}

// exe → 服务端
{"t":"tool_result","call_id":"c_1","ok":true,"result":{"stdout":"...","exit_code":0}}
{"t":"approval_prompt","call_id":"c_1","shown":true}     // 已弹窗给用户
```

**本地策略（照抄 Grok Bot 的三档，存服务端 + 设备端双份）**
| 档位 | 行为 |
|---|---|
| `ask`（默认） | 每次弹窗，显示命令原文 + 发起 Bot + 原因，60s 超时=拒绝 |
| `allow_always` | 对指定 Bot 的指定工具类放行，但高危命令仍问 |
| `never` | 直接拒绝，Bot 收到明确错误 |

**约束**
- 默认只开：读文件、截图、剪贴板读。写文件/执行命令默认 `ask`。
- 本地工具调用全部写 `origin=exe` 事件，审计可查。
- 本地桥离线时，Bot 调本地工具返回 `LOCAL_DEVICE_OFFLINE`（不是超时挂起）。

---

## 7. 语音

| 能力 | v1 做法 | 说明 |
|---|---|---|
| 语音转文字（输入框） | exe 录音（WebM/Opus 16k）→ `POST /v1/voice/transcribe` → 文本回填输入框，**用户可改再发** | 不做"边说边发" |
| 语音备忘（Bot 回复） | `POST /v1/voice/speak` → 音频 → 消息里的播放条 + 文本转写 | |
| 实时语音通话 | **v1 不做**（见功能对照表） | 需要 WebRTC + 流式 ASR/TTS + 打断，2–3 周 |

ASR/TTS 选型：优先腾讯云（你已有账号）；退化方案：服务器本地 `whisper.cpp`（4C8G 跑 `small` 实时率约 0.3–0.5，够用）+ `edge-tts`（免费）做 TTS。

---

## 8. 离线与错误

**exe 本地缓存**
- 每个会话缓存最近 200 条事件（SQLite）。
- 用户消息先入本地发件箱，连接恢复后按 `client_msg_id` 幂等重发。
- 草稿、屏幕最后一帧、Bot 列表缓存，冷启动秒开。

**错误模型**
```json
{"error":{"code":"BUDGET_PAUSED","message":"预算到 80%，已暂停","retriable":false,
          "details":{"job_id":"job_1","approval_id":"apr_2"}}}
```
- `409` 状态冲突（如重复 ack）→ 客户端忽略
- `401` 设备被踢 → 显示"已在新设备登录，请重新配对"
- `503` 服务器重启中 → 客户端指数退避，UI 显示"服务端重启中（预计 10s）"

---

## 9. 版本与兼容

- 路径带 `/v1`；服务端返回 `min_client_version`，过低时 exe 提示"请更新"（不硬禁用）。
- 能力协商：`server.caps` 决定 UI 是否显示"电脑"页/语音按钮/本地工具。
- 事件 schema 只做**加字段**，不改字段语义；新 kind 客户端不认识时静默忽略（前向兼容）。

---

## 10. 实现检查单

- [ ] 断网 30s 自动重连，事件不丢不重（脚本验证：服务端强制断连 100 次）
- [ ] 配对流程：错误码/过期/重复使用都能正确拒绝
- [ ] 证书指纹变化时拒绝连接并提示用户
- [ ] 屏幕流在 5Mbps 下限速自适应，且页面不可见时停流
- [ ] 本地桥 60s 未批准自动拒绝，Bot 收到明确错误
- [ ] 同一 `client_msg_id` 重发不产生重复消息
- [ ] 服务器重启 10s 内客户端自动恢复并补发
- [ ] 手机 PWA 只读聊天+审批可用（不依赖 exe 在线）
