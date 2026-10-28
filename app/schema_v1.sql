-- v1 新表（契约 01/02）。SQLite 写法，字段类型保持 PG 兼容：
-- INTEGER PRIMARY KEY AUTOINCREMENT -> 将来 bigserial；TEXT 存 ISO8601 时间戳。

-- ============================ 事件流（唯一真相） ============================
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,                 -- ISO8601 带时区
  bot_id        TEXT NOT NULL,
  session_id    TEXT,
  kind          TEXT NOT NULL,
  origin        TEXT NOT NULL,                 -- cloud | exe | user | system
  actor_json    TEXT NOT NULL,
  turn_id       TEXT,
  refs_json     TEXT,
  payload_json  TEXT NOT NULL,
  client_msg_id TEXT,                          -- 幂等键（用户消息）
  UNIQUE (kind, client_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_events_stream  ON events(id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_bot     ON events(bot_id, id);
CREATE INDEX IF NOT EXISTS idx_events_turn    ON events(turn_id, id);

CREATE TABLE IF NOT EXISTS session_snapshots (
  session_id   TEXT PRIMARY KEY,
  cursor       INTEGER NOT NULL,
  summary_json TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

-- ============================ 设备（exe / PWA） ============================
CREATE TABLE IF NOT EXISTS devices (
  id           TEXT PRIMARY KEY,
  name         TEXT,
  kind         TEXT NOT NULL,                  -- exe | pwa | wecom
  token_hash   TEXT NOT NULL,
  caps_json    TEXT,
  policy_json  TEXT,                           -- 本地桥三档策略
  last_seen    TEXT,
  revoked_at   TEXT,
  created_at   TEXT NOT NULL
);

-- ============================ Bot / 会话 ============================
CREATE TABLE IF NOT EXISTS bots (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  role          TEXT,
  persona       TEXT,
  model         TEXT,
  avatar        TEXT,
  tags_json     TEXT,
  pinned        INTEGER NOT NULL DEFAULT 0,
  hidden        INTEGER NOT NULL DEFAULT 0,
  section       TEXT,
  status        TEXT NOT NULL DEFAULT 'idle',  -- idle | working | paused
  created_at    TEXT NOT NULL,
  deleted_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,                 -- direct | group
  title         TEXT,
  admin_bot_id  TEXT,                          -- 群管理员（默认应答者）
  created_at    TEXT NOT NULL,
  deleted_at    TEXT
);

CREATE TABLE IF NOT EXISTS session_members (
  session_id    TEXT NOT NULL,
  member_id     TEXT NOT NULL,                 -- bot_id
  role          TEXT NOT NULL DEFAULT 'member',-- admin | member
  joined_at     TEXT NOT NULL,
  PRIMARY KEY (session_id, member_id)
);

-- ============================ 进程（一等公民） ============================
CREATE TABLE IF NOT EXISTS processes (
  id            TEXT PRIMARY KEY,              -- proc_...
  bot_id        TEXT NOT NULL,
  container_id  TEXT,
  pid           INTEGER,
  pgid          INTEGER,
  cmd           TEXT NOT NULL,
  cwd           TEXT,
  status        TEXT NOT NULL,                 -- running|exited|failed|killed|orphaned|timeout
  background    INTEGER NOT NULL DEFAULT 1,
  task_id       TEXT,
  turn_id       TEXT,
  log_path      TEXT NOT NULL,
  log_bytes     INTEGER NOT NULL DEFAULT 0,
  progress      REAL,
  progress_hint TEXT,
  exit_code     INTEGER,
  started_at    TEXT NOT NULL,
  exited_at     TEXT,
  last_tick_at  TEXT,
  max_runtime_s INTEGER NOT NULL DEFAULT 21600
);
CREATE INDEX IF NOT EXISTS idx_proc_bot    ON processes(bot_id, status);
CREATE INDEX IF NOT EXISTS idx_proc_status ON processes(status);

CREATE TABLE IF NOT EXISTS process_notes (
  process_id  TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  note        TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (process_id, seq)
);

-- ============================ 应用连接 / 设置 / 保险库 ============================
CREATE TABLE IF NOT EXISTS apps (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  kind         TEXT NOT NULL DEFAULT 'app',   -- channel | app | tool | mcp
  connected    INTEGER NOT NULL DEFAULT 0,
  connected_at TEXT
);

-- 注意：legacy 已有一张 settings(key,value) 表（ui/store.py），这里用 app_settings 避让。
CREATE TABLE IF NOT EXISTS app_settings (
  key         TEXT PRIMARY KEY,
  value_json  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- 配对码：一次性、5 分钟有效（新设备凭它换长期设备令牌）
CREATE TABLE IF NOT EXISTS pairing_codes (
  code        TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);

-- 工作台免登录票：一次性、120 秒（换 /app/ 的签名 token）
CREATE TABLE IF NOT EXISTS legacy_sso_tickets (
  ticket      TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  token       TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT
);
CREATE TABLE IF NOT EXISTS vault_items (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,                  -- login | card | contact
  title       TEXT NOT NULL,
  fields_enc  TEXT NOT NULL,                  -- Fernet 密文
  created_at  TEXT NOT NULL
);


-- 每个 Bot 的长期记忆（跨会话），去重键 = bot_id + content_hash
CREATE TABLE IF NOT EXISTS memories (
  id              TEXT PRIMARY KEY,
  bot_id          TEXT NOT NULL,
  session_id      TEXT,
  kind            TEXT NOT NULL DEFAULT 'fact',   -- fact | preference | decision | note
  content         TEXT NOT NULL,
  tags            TEXT NOT NULL DEFAULT '',
  importance      INTEGER NOT NULL DEFAULT 1,
  topic           TEXT NOT NULL DEFAULT '',     -- 1..3
  content_hash    TEXT NOT NULL,
  source_event_id INTEGER,
  created_at      TEXT NOT NULL,
  last_used_at    TEXT,
  use_count       INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_bot_hash ON memories(bot_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_memories_bot_time ON memories(bot_id, created_at DESC);
