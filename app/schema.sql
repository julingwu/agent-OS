-- M0 schema: 11 tables + 1 state-machine guard trigger
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  plan TEXT DEFAULT 'internal',
  state TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO tenants (id, name, plan) VALUES ('TENANT-HQ', '集团总部', 'internal');

CREATE TABLE IF NOT EXISTS legal_entities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  type TEXT NOT NULL DEFAULT 'group_hq',
  parent_entity_id TEXT,
  data_boundary TEXT DEFAULT 'transparent',
  budget_mode TEXT DEFAULT 'consolidated',
  governance_mode TEXT DEFAULT 'group_direct',
  pack_policy TEXT DEFAULT 'follow_group',
  state TEXT DEFAULT 'active'
);
INSERT OR IGNORE INTO legal_entities (id, name, type) VALUES ('LE-GROUP-HQ', '集团总部', 'group_hq');

CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  dept_id TEXT,
  parent_position_id TEXT,
  title TEXT NOT NULL,
  jd_content TEXT,
  pack_id TEXT,
  tool_acl JSON,
  dir_acl JSON,
  acceptance_template JSON,
  capability_matrix JSON,
  state TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS ai_employees (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  name TEXT NOT NULL,
  position_id TEXT REFERENCES positions(id),
  model TEXT,
  role_id TEXT,
  soul TEXT,
  focus TEXT,
  token_hash TEXT,
  status TEXT DEFAULT 'online'
);

CREATE TABLE IF NOT EXISTS memory_entries (
  id TEXT PRIMARY KEY,
  employee_id TEXT NOT NULL REFERENCES ai_employees(id),
  scope TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mem_emp_scope ON memory_entries(employee_id, scope);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  group_id TEXT,
  owner_position_id TEXT REFERENCES positions(id),
  task_type TEXT,
  title TEXT,
  state TEXT NOT NULL DEFAULT 'todo',
  pack_id TEXT,
  budget_id TEXT,
  run_count INTEGER DEFAULT 0,
  project_id TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS acceptance_criteria (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  criterion TEXT NOT NULL,
  check_type TEXT NOT NULL,
  check_ref TEXT,
  is_blocking INTEGER DEFAULT 1,
  evidence_required INTEGER DEFAULT 0,
  weight REAL DEFAULT 1.0,
  risk_level TEXT DEFAULT 'low',
  is_compliance INTEGER DEFAULT 0,
  acceptance_layer TEXT DEFAULT 'system',
  state TEXT DEFAULT 'pending',
  checked_by TEXT,
  is_deterministic INTEGER DEFAULT 1,
  note TEXT,
  seq INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_criteria_task ON acceptance_criteria(task_id, seq);

CREATE TABLE IF NOT EXISTS evidence_refs (
  id TEXT PRIMARY KEY,
  owner_type TEXT,
  owner_id TEXT,
  file_path TEXT,
  content_snippet TEXT,
  file_checksum TEXT,
  trace_snippet TEXT,
  line_no INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_evidence_owner ON evidence_refs(owner_type, owner_id);

CREATE TABLE IF NOT EXISTS task_runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id),
  run_seq INTEGER NOT NULL,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  state TEXT NOT NULL DEFAULT 'spawned',
  session_id TEXT,
  pid INTEGER,
  hb_seq INTEGER DEFAULT 0,
  last_hb_at TIMESTAMP,
  est_next_cost REAL,
  runner TEXT,
  actor_identity TEXT,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  exit_code INTEGER,
  usage_tokens INTEGER DEFAULT 0,      -- cumulative per session, as reported
  usage_baseline INTEGER DEFAULT 0,    -- session usage when this run started
  error TEXT,                          -- human-readable failure reason (v3.1)
  mcp_fingerprint TEXT,                -- hash of the authority bound to this ACP session
  UNIQUE(task_id, run_seq)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES task_runs(id),
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  art_seq INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  content TEXT,
  checksum TEXT NOT NULL,
  bytes INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(run_id, art_seq)
);

CREATE TABLE IF NOT EXISTS budgets (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  identity_id TEXT,                  -- one budget row per agent, per entity
  task_id TEXT REFERENCES tasks(id),
  limit_tokens INTEGER NOT NULL DEFAULT 0,
  reserved_tokens INTEGER NOT NULL DEFAULT 0,
  used_tokens INTEGER NOT NULL DEFAULT 0,
  limit_amount REAL DEFAULT 0,          -- money budget for decision packages
  reserved_amount REAL DEFAULT 0,
  used_amount REAL DEFAULT 0,
  state TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS ai_audit_logs (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  task_id TEXT,
  run_id TEXT,
  verb TEXT,
  layer TEXT,
  actor TEXT,
  action TEXT,
  target TEXT,
  payload JSON,
  response_json JSON,
  idempotency_key TEXT UNIQUE,
  replay_of TEXT,
  is_deterministic INTEGER DEFAULT 1,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tasks_pack ON tasks(pack_id, state);
CREATE INDEX IF NOT EXISTS idx_runs_identity ON task_runs(actor_identity);
CREATE INDEX IF NOT EXISTS idx_audit_task ON ai_audit_logs(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_run ON ai_audit_logs(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_replay ON ai_audit_logs(replay_of);


-- ============ M0.5: decision package + finance/audit dual track ============
-- A decision is a packet: options, a recommendation, a price, a risk level and
-- how reversible it is. The filter decides who has to look at it; the CEO only
-- ever sees what survives the filter.
CREATE TABLE IF NOT EXISTS decision_packages (
  id TEXT PRIMARY KEY,
  legal_entity_id TEXT NOT NULL REFERENCES legal_entities(id),
  task_id TEXT REFERENCES tasks(id),
  created_by TEXT,
  title TEXT NOT NULL,
  summary TEXT,
  options JSON,
  recommendation TEXT,
  amount REAL DEFAULT 0,
  currency TEXT DEFAULT 'CNY',
  risk_level TEXT DEFAULT 'low',          -- low / medium / high
  reversibility TEXT DEFAULT 'reversible',-- reversible / costly / irreversible
  is_compliance INTEGER DEFAULT 0,
  confidence REAL DEFAULT 1.0,            -- the agent's own confidence 0..1
  deadline TEXT,
  state TEXT DEFAULT 'draft',             -- draft/auto_approved/escalated_manager/escalated_ceo/approved/rejected/blocked
  route TEXT,                             -- what the filter decided
  matched_rule TEXT,
  filter_reason TEXT,
  decided_by TEXT,
  decided_at TIMESTAMP,
  decision_note TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decision_state ON decision_packages(state, legal_entity_id);
CREATE INDEX IF NOT EXISTS idx_decision_task ON decision_packages(task_id);

CREATE TABLE IF NOT EXISTS decision_filter_rules (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  priority INTEGER NOT NULL,
  when_json JSON NOT NULL,
  action TEXT NOT NULL,                   -- auto_approve / escalate_manager / escalate_ceo / block
  reason TEXT,
  active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS decision_finance (
  id TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES decision_packages(id),
  budget_id TEXT,
  amount REAL NOT NULL DEFAULT 0,
  currency TEXT DEFAULT 'CNY',
  available_before REAL DEFAULT 0,
  reserved_after REAL DEFAULT 0,
  verdict TEXT NOT NULL,                  -- ok / over_budget / unbudgeted
  note TEXT,
  checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_decision_finance ON decision_finance(decision_id);

INSERT OR IGNORE INTO decision_filter_rules (id, name, priority, when_json, action, reason) VALUES
 ('FR-01', '合规事项必须 CEO 拍板', 10, '{"compliance": true}', 'escalate_ceo', '涉及合规，人工不可下放'),
 ('FR-02', 'AI 自评置信度过低', 20, '{"confidence_lt": 0.5}', 'escalate_ceo', '模型自己没把握，不宜自动放行'),
 ('FR-03', '不可逆且金额较大', 30, '{"reversibility_in": ["irreversible"], "amount_gte": 20000}', 'escalate_ceo', '不可逆 + 金额过线'),
 ('FR-04', '金额超过 5 万', 40, '{"amount_gte": 50000}', 'escalate_ceo', '大额支出'),
 ('FR-05', '高风险决策', 50, '{"risk_in": ["high"]}', 'escalate_ceo', '高风险'),
 ('FR-06', '金额超过 5 千', 60, '{"amount_gte": 5000}', 'escalate_manager', '中等金额，主管可批'),
 ('FR-07', '中风险决策', 70, '{"risk_in": ["medium"]}', 'escalate_manager', '中风险，主管可批'),
 ('FR-08', '小额、可逆、低风险', 80, '{"amount_lt": 5000, "risk_in": ["low"], "reversibility_in": ["reversible"]}', 'auto_approve', '在授权范围内，自动放行并留痕'),
 ('FR-99', '默认下放主管', 999, '{}', 'escalate_manager', '无规则命中，按默认路由');

-- ============ WeCom channel: user binding + push ledger + mock outbox ============
CREATE TABLE IF NOT EXISTS wecom_users (
  wecom_userid TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL REFERENCES identities(id),
  name TEXT,
  state TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wecom_pushes (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- task_review / decision_ceo
  target_id TEXT NOT NULL,
  wecom_userid TEXT NOT NULL,
  msg_id TEXT,
  status TEXT DEFAULT 'pending',      -- pending / sent / failed
  error TEXT,
  sent_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(kind, target_id, wecom_userid)
);

CREATE TABLE IF NOT EXISTS wecom_outbox (
  id TEXT PRIMARY KEY,
  to_user TEXT,
  msgtype TEXT,
  body JSON,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wecom_seen (
  msg_key TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============ WeCom native approval sign-off ============
CREATE TABLE IF NOT EXISTS wecom_approvals (
  sp_no TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  criterion_id TEXT NOT NULL,
  userid TEXT NOT NULL,
  template_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',      -- pending / approved / rejected
  result TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============ Octo bridge: bots, event dedupe, reply chains ============
CREATE TABLE IF NOT EXISTS octo_bots (
  bot_uid TEXT PRIMARY KEY,
  bot_token TEXT,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL,                 -- pm / frontend / backend / content
  identity_id TEXT,
  budget_calls INTEGER DEFAULT 200,
  used_calls INTEGER DEFAULT 0,
  muted_until TIMESTAMP,
  state TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS octo_seen (
  message_id TEXT PRIMARY KEY,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS octo_chains (
  channel_id TEXT PRIMARY KEY,
  last_from TEXT,
  bot_replies INTEGER DEFAULT 0,
  window_start TIMESTAMP
);

-- ============ business packs: template + defaults per line of business ============
CREATE TABLE IF NOT EXISTS packs (
  id TEXT PRIMARY KEY,               -- content-v1 / code-v1 / review-v1 / finance-v1
  name TEXT NOT NULL,
  template TEXT NOT NULL,            -- which acceptance template to apply
  description TEXT,
  default_budget INTEGER DEFAULT 30000,
  state TEXT DEFAULT 'active'
);

INSERT OR IGNORE INTO packs (id, name, template, description, default_budget) VALUES
 ('content-v1', '内容营销包', 'content', '文案、文章、投放素材', 25000),
 ('code-v1',    '代码交付包', 'code',    '脚本、工具、自动化', 30000),
 ('code-local-v1', '本地代码包', 'code', '不联网的脚本、工具', 25000),
 ('review-v1',  '质检评审包', 'review',  '对他人产出做评审', 45000),
 ('finance-v1', '财务审核包', 'finance', '费用审核、对账、报销复核', 30000);

-- ============ M1: identities and replay protection ============
CREATE TABLE IF NOT EXISTS identities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  role TEXT NOT NULL,                 -- system / ceo / manager / agent
  legal_entity_id TEXT,
  tenant_id TEXT DEFAULT 'TENANT-HQ',
  employee_id TEXT,                  -- optional link to ai_employees (entity + pack)
  secret TEXT NOT NULL,               -- HMAC key (see DESIGN_GAPS §20)
  scopes JSON,
  state TEXT DEFAULT 'active',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auth_nonces (
  nonce TEXT PRIMARY KEY,
  identity_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nonce_ident ON auth_nonces(identity_id, ts);

-- ============ scheduling: one run per task, one run per session ============
CREATE TABLE IF NOT EXISTS run_locks (
  resource TEXT PRIMARY KEY,          -- 'task:<id>' | 'session:<id>'
  run_id TEXT NOT NULL,
  acquired_at TEXT,
  expires_at TEXT
);

-- State machine hard gate: the transition table lives in the database, so a
-- direct UPDATE from any other connection cannot bypass it.
DROP TRIGGER IF EXISTS trg_tasks_state_guard;
CREATE TRIGGER trg_tasks_state_guard
BEFORE UPDATE OF state ON tasks
WHEN NEW.state <> OLD.state AND NOT (
      (OLD.state = 'todo'           AND NEW.state = 'assigned')
   OR (OLD.state = 'assigned'       AND NEW.state IN ('in_progress','failed'))
   OR (OLD.state = 'in_progress'    AND NEW.state IN ('pending_review','failed','cancelled'))
   OR (OLD.state = 'pending_review' AND NEW.state IN ('completed','rejected'))
   OR (OLD.state = 'rejected'       AND NEW.state IN ('in_progress','pending_review'))
   OR (OLD.state = 'failed'         AND NEW.state = 'in_progress')
)
BEGIN
  SELECT RAISE(ABORT, 'IllegalStateTransition');
END;
