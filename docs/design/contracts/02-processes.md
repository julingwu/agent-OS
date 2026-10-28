# 契约 02 · 进程模型（Process Model v1）

> 状态：**定稿待实现**｜依据：Grok Bot 截图里 Bot 原话——"监控又断了，但回测进程本身还在跑"
> 一句话：**任务是有终点的委托，进程是可能没有终点的东西。进程是一等公民，不是任务的子字段。**

---

## 1. 为什么必须单独建模（第一性原理）

截图暴露了三个真实需求：

1. 用户关掉窗口 → 进程不能死（否则长任务白跑）
2. 监控/连接断了 → 进程不能死（否则网络抖动就毁掉几小时的计算）
3. Bot 重启 / 换一个 turn → 进程不能死（"我继续盯着"意味着它只是重新 attach）

**推论**：进程的生命周期**独立于**：客户端连接、会话 turn、Bot 会话、甚至服务端进程（在容器活着的前提下）。
所以不能把进程塞进 `jobs` 表当子状态，必须有独立的 `processes` 表和独立的 supervisor。

---

## 2. 生命周期

```
                 proc_start(background=true)
                            │
                            ▼
                        ┌────────┐   exit 0    ┌──────────┐
                        │running │────────────▶│ exited   │
                        └────┬───┘             └──────────┘
                            │  exit≠0              ▲
                            │                      │
             proc_kill ─────┼──────────────────────┤ killed
                            │                      │
             容器重启/崩溃   │                      │
                            ▼                      │
                        ┌──────────┐               │
                        │ orphaned │──adopt/重试──▶│
                        └──────────┘               │
                                                   │
  超时（可选 max_runtime）──────────────────────────┘ timeout
```

| 状态 | 含义 | UI |
|---|---|---|
| `running` | 活着，`pid` 有效 | 绿点 + 已运行时长 |
| `exited` | 正常退出 | 灰点 + exit 0 + 产物 |
| `failed` | 非 0 退出 | 红点 + 最后 20 行 stderr |
| `killed` | 被 kill | 灰点 + 谁杀的 |
| `orphaned` | 容器重启后找不到 pid | 黄点 + "已中断，可重试" |
| `timeout` | 超过 `max_runtime_s` 被杀 | 黄点 |

**规则**
- 进程启动即写 `process.started` 事件；退出写 `process.exited`。
- **tick 事件由 supervisor 定时产生**（默认 15s），不经过模型 → 零 token 的进度展示。
- 进程输出**不进事件表**：写 `logs/<process_id>.log`，事件只记 `log_path + 已写字节数`。
- Bot 通过 `proc_tail`（读最后 N 行）或 `proc_ps` 获取现状，不需要一直占着 turn。

---

## 3. 工具（agent 可调用，服务端实现）

| 工具 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `proc_start` | `cmd`(str\|list), `cwd?`, `env?`, `max_runtime_s?`, `background=true` | `{process_id, pid}` | 默认后台；`background=false` 时等待结束并返回输出 |
| `proc_ps` | `all?` | `[{process_id, pid, cmd, status, elapsed_s, cpu, mem}]` | 进程列表 |
| `proc_tail` | `process_id, lines=80, offset?` | `{lines[], next_offset, eof}` | 读日志尾；支持增量 |
| `proc_kill` | `process_id, signal?` | `{ok, exit_code}` | 先 TERM，5s 后 KILL |
| `proc_wait` | `process_id, timeout_s=60` | `{status, exit_code?, elapsed_s}` | 阻塞等待（有上限，避免占死 turn） |

**实现要点**
- 用 `setsid` 起进程（新 session + 新进程组），日志重定向到文件，父进程不持有管道 → **服务端重启不影响它**。
- 记录 `pgid`，kill 时杀整个进程组（照抄现有 Windows 的 kill_tree 教训）。
- 容器内 pid namespace：`pid` 是容器内 pid；服务端另记 `container_id`，用 `docker top` 校验存活。
- `max_runtime_s` 默认 6h，上限 24h（自用放宽），到期 TERM→KILL。

---

## 4. Supervisor 与对账（重启不丢的关键）

**server 启动时**
1. 读 `processes WHERE status='running'`；
2. 对每个：`docker top <container> | grep <pid>` 或读 `/proc/<pid>/stat`（容器内 inspect）；
3. 在 → 保持 running，补一条 `process.tick`；不在 → 置 `orphaned` 并写事件；
4. `orphaned` 的进程给 Bot 一条系统提示（下次 turn 时注入"你有 2 个进程在重启中丢了，分别是…"）。

**运行中**
- 每 15s：刷新 `cpu/mem/elapsed`，写 `process.tick`（**合并成一条批量事件**，避免事件表膨胀）。
- 日志写入超过 50MB：轮转 `.1/.2`，只保留最近 2 份。

**容器重启/重建**
- 容器重建 = 所有进程丢失（这是 Grok Bot "Update computer" 的语义：保留文件/登录态，不保留进程）。
- 重建前先把 running 进程置 `orphaned`，并给每个进程留一条"恢复建议"（命令原文，便于一键重跑）。

---

## 5. 长任务的进度语义（截图里"大约已跑到 2026 年 4-5 月那段"）

进度**不是模型猜的**，三种来源按优先级：

| 来源 | 例 | 实现 |
|---|---|---|
| 进程自己输出 | 脚本打印 `PROGRESS 43%` 或 `[2026-04]` | `log_pattern` 配置：正则抓进度 |
| 输出字节数 vs 已知总量 | 下载/编译 | 若有 `total_bytes` 标签则估算 |
| 无信息 | 黑盒脚本 | 只显示"已运行 1h23m" |

Bot 只会在这几种时机被唤起（花 token）：
- 进程退出
- 进度跨过 25/50/75/100%
- 用户发消息
- `needs_human`
- 每 30 分钟一次"静默检查"（可关，默认关）

> 这条设计直接决定长任务成本：**进程跑 3 小时，模型只被叫醒 4–6 次**，而不是每 15 秒一次。

---

## 6. 安全

| 风险 | 措施 |
|---|---|
| 进程失控烧 CPU | cgroup 限制（容器级 1.5 核）；`max_runtime_s`；`nice 10` |
| 进程写满磁盘 | 日志轮转；`/workspace` 配额（默认 20GB）；写满前告警 |
| 危险命令 | 命令**不等于**工具权限：`rm -rf /`、`curl\|sh` 这类入库"高危模式"，命中则要求审批（复用现有 `redline.py` 思路） |
| 后台进程逃逸容器 | 容器内 PID namespace 隔离；容器本身是不可逃逸边界 |
| 用户不知道它在跑什么 | 电脑页进程列表 + 每条 `proc_start` 在聊天里留痕（工具条可展开看完整命令） |

---

## 7. DDL

```sql
CREATE TABLE processes (
  id            TEXT PRIMARY KEY,           -- proc_01J...
  bot_id        TEXT NOT NULL,
  container_id  TEXT,
  pid           INTEGER,
  pgid          INTEGER,
  cmd           TEXT NOT NULL,
  cwd           TEXT,
  status        TEXT NOT NULL,              -- running|exited|failed|killed|orphaned|timeout
  background    INTEGER NOT NULL DEFAULT 1,
  task_id       TEXT,                       -- 可选：哪个任务起的（不参与生命周期）
  turn_id       TEXT,                       -- 可选：哪个 turn 起的
  log_path      TEXT NOT NULL,
  log_bytes     INTEGER DEFAULT 0,
  progress      REAL,                       -- 0..1，可空
  progress_hint TEXT,                       -- "2026-04"
  exit_code     INTEGER,
  started_at    TEXT NOT NULL,
  exited_at     TEXT,
  last_tick_at  TEXT,
  max_runtime_s INTEGER DEFAULT 21600
);
CREATE INDEX idx_proc_bot    ON processes(bot_id, status);
CREATE INDEX idx_proc_status ON processes(status);

CREATE TABLE process_notes (                -- 进程丢失后的恢复建议 / 人工备注
  process_id  TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  note        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (process_id, seq)
);
```

---

## 8. 实现检查单

- [ ] `proc_start` 后用 `ps -o pid,pgid,sid` 确认进程组独立（setsid 生效）
- [ ] 杀掉**服务端进程**，进程仍在跑（`kill -9 <server>` 后 `docker top` 仍见 pid）
- [ ] 服务端重启后，`processes` 表里 running 的条目被正确 adopt 或置 orphaned
- [ ] 日志 50MB 轮转，`proc_tail` 跨轮转仍能读（`.1` 文件回退读）
- [ ] 进程跑 2 小时期间，模型调用次数 ≤ 8（用 usage 表验证）
- [ ] 容器重建后，进程置 orphaned 并给出一键重跑命令
- [ ] `rm -rf /` 命中高危规则 → 触发审批而不是直接执行
