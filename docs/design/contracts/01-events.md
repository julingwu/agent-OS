# 契约 01 · 事件协议（Event Protocol v1）

> 状态：**定稿待实现**｜适用：服务端 ↔ exe ↔ 手机端（PWA/企微）
> 一句话：**事件流是唯一真相，四个页面都是它的投影。** 任何 UI 状态都必须能从「快照 + 事件流」重建，不允许页面各自查库拼状态。

---

## 1. 原则（违反任何一条，后面必然返工）

| # | 原则 | 后果 |
|---|---|---|
| P1 | 事件只追加，不改不删 | 可回放、可审计、可断线续传 |
| P2 | 每条事件有全局单调递增 `id`，客户端游标就是 `id` | 重连只需带 `since=id` |
| P3 | 写入方只有服务端（用户意图先变成服务端事件） | 避免两端各自生成事件导致顺序不一致 |
| P4 | 事件是"已发生的事实"，不是"指令" | 指令走 REST/WS 请求，结果才落事件 |
| P5 | 大对象（文件、截图、日志）不塞事件体，只放引用 | 事件表要保持小、快、可扫 |
| P6 | 会话内事件顺序 = `id` 顺序；跨会话不做全局因果假设 | 避免分布式排序难题 |
| P7 | 每条事件带 `origin`（cloud/exe/user/system） | 本地桥执行的动作也要进同一条流 |

---

## 2. 信封（Envelope）

```jsonc
{
  "id": 1048577,                    // bigint，全局单调递增，游标就是它
  "ts": "2026-10-26T22:41:03.218+08:00",
  "bot_id": "bot_01J...",           // 必填：事件归属的 Bot
  "session_id": "ses_01J...",       // 可空：群聊/单聊会话
  "kind": "tool.call",              // 见第 3 节
  "origin": "cloud",                // cloud | exe | user | system
  "actor": {"type": "bot", "id": "bot_01J..."},
  "turn_id": "turn_01J...",         // 一次"用户发言→Bot 收尾"为一个 turn
  "refs": {                         // 只放引用，不放内容
    "message_id": null,
    "job_id": "job_01J...",
    "process_id": "proc_01J...",
    "artifact_id": "art_01J...",
    "approval_id": null,
    "reply_to": null
  },
  "payload": { }                    // 按 kind 定义的 schema
}
```

**约定**
- `id` 用 SQLite `INTEGER PRIMARY KEY AUTOINCREMENT`（将来迁 PG 就是 `bigserial`）。
- `ts` 由服务端生成，ISO8601 带时区；**不要**用客户端时间排列。
- `bot_id` 必填：连"用户全局设置变了"这类事件也挂到对应 Bot 下，简化权限过滤。
- 单事件体 ≤ 8KB；超了就把内容落文件，事件里放 `artifact_id`。

---

## 3. 事件类型（v1 闭集）

> 闭集：实现期只允许这些 kind；新增必须改本文档 + 加迁移。

### 3.1 会话与消息
| kind | payload 关键字段 | 投影到 |
|---|---|---|
| `message.user` | `text, attachments[], client_msg_id` | 聊天气泡（右） |
| `message.bot` | `text, parts[]（text/thought/tool-ref/artifact-ref/voice-memo）` | 聊天气泡（左） |
| `message.system` | `text, level` | 聊天里的系统提示条 |
| `message.edited` / `message.deleted` | `target_id, text?` | 原地改写 |
| `reaction.set` | `target_id, emoji, on` | 气泡上的表情 |
| `draft.created` | `channel(email/slack), to, subject?, body, draft_id` | **可编辑草稿卡**（发送/丢弃） |
| `typing` | `state: on/off` | 输入中指示（不入库，仅 WS 推送） |

### 3.2 回合与工具
| kind | payload | 说明 |
|---|---|---|
| `turn.started` | `input_event_id` | 一个 turn 开始 |
| `turn.ended` | `status: done/failed/needs_human/interrupted, reason?` | 结束原因落库 |
| `thought.delta` | `text` | **只进日志，不进聊天**（省钱、不刷屏） |
| `tool.call` | `tool, args, call_id` | 聊天里折叠成"正在用 xxx" |
| `tool.result` | `call_id, ok, summary, artifact_refs[]` | 折叠区内容 |
| `needs_human` | `question, options[], screenshot_ref?` | **冒泡**到手机/桌面 |
| `error` | `code, message, retriable` | 系统条 + 告警 |

### 3.3 电脑（执行体）
| kind | payload | 说明 |
|---|---|---|
| `computer.state` | `status: cold/waking/awake/sleeping/error, screen: {w,h,fd}` | 电脑页状态 |
| `process.started` | `process_id, cmd, cwd, pid, background` | 进程列表新增 |
| `process.tick` | `process_id, cpu, mem, elapsed_s, log_bytes` | 无模型参与的进度心跳 |
| `process.exited` | `process_id, exit_code, signal?, duration_s` | 触发 Bot 汇报 |
| `process.killed` | `process_id, by` | |
| `file.created` / `file.updated` / `file.deleted` | `path, bytes, artifact_id?` | 资料库/电脑页文件树 |
| `screen.frame` | **不进事件表** | 走独立二进制通道，仅推送最新帧 |

### 3.4 审批与预算
| kind | payload |
|---|---|
| `approval.requested` | `approval_id, risk, action, preview, options[]` |
| `approval.decided` | `approval_id, decision: allow/deny/allow_always, by` |
| `budget.updated` | `job_id?, used, limit, ratio, state: ok/warn/paused` |
| `secret.requested` | `secret_id, prompt, fields[]`（值永不入事件、不入模型上下文） |

### 3.5 协作（多 Bot）
| kind | payload |
|---|---|
| `handoff.created` | `from_bot, to_bot, task, acceptance, parent_turn_id` |
| `handoff.accepted` | `handoff_id` |
| `handoff.result` | `handoff_id, verdict, artifact_refs[]` |
| `group.message` | 复用 `message.*`，`session_id` 指向群；`kind` 不变 |

### 3.6 资料库 / 技能 / 例行任务
| kind | payload |
|---|---|
| `memory.updated` | `memory_id, action: add/update/archive, kind: persona/fact/experience, preview` |
| `skill.created` / `skill.updated` | `skill_id, name, version, source_run?` |
| `routine.created` / `routine.updated` / `routine.triggered` / `routine.finished` | `routine_id, schedule?, run_id?, verdict?` |
| `artifact.attached` | `artifact_id, name, mime, bytes, previewable` |

---

## 4. 游标、快照与续传（断线重连的全部秘密）

```
客户端首次启动：GET /v1/bootstrap { bots[], sessions[], server:{caps,limits} } → 不含事件
打开某会话：    GET /v1/sessions/{id}/snapshot?limit=50
                → { events:[...], cursor: 1048570, has_more: false }
                → 渲染历史（最后 50 条足够首屏）
订阅实时：      WSS /v1/stream?since=1048570
                → 服务端先补发 since 之后的历史（分批，每帧≤200条）
                → 再持续推新事件
断线重连：      带最后已确认 id 重连；服务端从该 id 之后补发
```

**必须处理的三种坑**
1. **补发期间又有新事件**：先补发再订阅的间隙会漏 → 服务端在同一条连接里先推 backlog（标记 `replay: true`），推完再切实时，中间不换连接。
2. **重复事件**：客户端以 `id` 去重（幂等渲染）。
3. **事件太多**：单会话历史用分页快照（`before_id`）；实时流只在 backlog > 500 时提示"正在同步历史"。

**确认机制**：客户端每处理完一批发 `ack{id}`；服务端据此裁剪每个设备的内存队列（不落库）。

---

## 5. 聊天投影规则（哪些事件变成什么）

| 事件 | 聊天里的呈现 |
|---|---|
| `message.user/bot` | 气泡 |
| `tool.call` + `tool.result` | 一个可折叠的"工具过程"条（默认收起，显示 `工具名 · 耗时 · 成功/失败`） |
| `thought.delta` | **不显示**（可开"调试模式"查看） |
| `process.started/tick/exited` | 一条**状态条**（不在消息流里，在输入框上方） |
| `needs_human` / `approval.requested` / `secret.requested` | **卡片**（置顶+冒泡通知），必须阻断式交互 |
| `artifact.attached` / `file.created` | 文件卡（可预览/下载/继续改） |
| `draft.created` | 可编辑草稿卡（发送/丢弃） |
| `error` | 系统条（红） |
| `budget.updated` | 状态条右侧小徽章（≥80% 变红） |

> **状态条是系统事件渲染的，不是模型生成的文案。** 这样断线时还能显示最后已知进度，也不烧 token。

---

## 6. 保留与压缩

| 数据 | 保留 | 说明 |
|---|---|---|
| 事件原文 | 90 天 | 超过后按 turn 折叠成 `turn_summary` 事件（1 条替 N 条） |
| 聊天消息 | 永久（折叠后仍在） | 用户可见内容不能丢 |
| 工具过程 | 30 天 | 折叠后只留摘要 |
| 进程/文件事件 | 30 天 | |
| 截图帧 | 不落库 | 只留"关键帧"（needs_human 时的那张） |
| 日志文件 | 按进程：保留最近 20 个 / 每个 50MB 轮转 | 放磁盘，事件只记引用 |

**快照表**：`session_snapshots(session_id, cursor, summary_json, updated_at)` —— 每次 turn 结束更新，用于秒开长会话。

---

## 7. DDL（SQLite，语法保持 PG 兼容）

```sql
CREATE TABLE events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,                    -- ISO8601+08:00
  bot_id        TEXT NOT NULL,
  session_id    TEXT,
  kind          TEXT NOT NULL,
  origin        TEXT NOT NULL,                    -- cloud|exe|user|system
  actor_json    TEXT NOT NULL,
  turn_id       TEXT,
  refs_json     TEXT,
  payload_json  TEXT NOT NULL,
  client_msg_id TEXT,                             -- 用户消息幂等键
  UNIQUE(kind, client_msg_id)                     -- 幂等：同一 client_msg_id 只落一次
);
CREATE INDEX idx_events_stream  ON events(id);
CREATE INDEX idx_events_session ON events(session_id, id);
CREATE INDEX idx_events_bot     ON events(bot_id, id);
CREATE INDEX idx_events_turn    ON events(turn_id, id);

CREATE TABLE session_snapshots (
  session_id   TEXT PRIMARY KEY,
  cursor       INTEGER NOT NULL,
  summary_json TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE devices (                            -- exe / 手机端
  id           TEXT PRIMARY KEY,
  name         TEXT,
  kind         TEXT,                             -- exe|pwa|wecom
  token_hash   TEXT NOT NULL,
  caps_json    TEXT,                             -- 本地能力声明
  last_seen    TEXT,
  created_at   TEXT NOT NULL
);
```

**事件写入函数（唯一入口）**

```python
def emit(conn, *, bot_id, kind, payload, session_id=None, origin="cloud",
         actor=None, turn_id=None, refs=None, client_msg_id=None) -> int:
    """所有事件必须走这里：校验 kind 闭集、payload schema、大小上限、写库、
    然后 publish 到内存广播器（给 WSS）。禁止各处直接 INSERT。"""
```

---

## 8. 实现检查单（写完代码逐条打勾）

- [ ] 所有写事件路径都走 `emit()`，全仓 grep 无第二处 `INSERT INTO events`
- [ ] 断线重连测试：同一条连接内先 backlog 再实时，无重复无丢失（脚本可验）
- [ ] 客户端幂等：同一 `client_msg_id` 重发 3 次只落 1 条、只回复 1 次
- [ ] `thought.delta` 不出现在聊天投影里
- [ ] 事件 >8KB 时自动降级为 artifact 引用
- [ ] turn 折叠任务每天跑一次，折叠后历史仍可完整显示
- [ ] 权限过滤：A 用户订阅不到 B 的 bot 事件（即使知道 bot_id）
