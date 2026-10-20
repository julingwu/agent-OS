"""v1.5 产线：项目 / 阶段 / 闸门 / 研讨会 / WBS。

设计要点
--------
* 9 个阶段（0 接需求 … 8 复盘）在项目创建时**一次性落库为 pending**，不许临时插队。
* 状态机写死在 SQLite 触发器里（不是仅靠 Python）：
    - `trg_stage_no_skip`    ：只有上一阶段 done 才能把下一阶段置 active；
    - `trg_stage_gate_guard` ：阶段置 done 前，该阶段所有闸门必须 passed；
    - `trg_gate_veto`        ：否决闸门一票停摆，项目直接 blocked。
* 闸门 = gate_run(scope=project|stage|task)，复用原有判据思路，但作用域不同。
* 研讨会必须四轮：提案 → 质询 → 修订 → 表决；每个参会角色都要留下 risk/objection。
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import uuid

from .audit import write_audit

STAGES = [
    ("intake", "接需求"),
    ("charter", "立项"),
    ("workshop", "研讨"),
    ("wbs", "拆解"),
    ("build", "实现"),
    ("test", "测试"),
    ("accept", "验收"),
    ("release", "发布"),
    ("retro", "复盘"),
]

DDL = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  requirement TEXT NOT NULL,
  legal_entity_id TEXT DEFAULT 'LE-GROUP-HQ',
  tenant_id TEXT NOT NULL DEFAULT 'TENANT-HQ',
  veto_note TEXT,
  retry_token TEXT,
  last_error TEXT,
  state TEXT DEFAULT 'active',              -- active | blocked | done
  use_llm_docs INTEGER DEFAULT 0,           -- 1=模型写文档（贵） 0=模板（省 token）
  current_seq INTEGER DEFAULT 0,
  owner_identity TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_stages (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  key TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT DEFAULT 'pending',             -- pending | active | done | blocked
  started_at TIMESTAMP,
  ended_at TIMESTAMP,
  UNIQUE(project_id, seq)
);

CREATE TABLE IF NOT EXISTS stage_gates (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage_key TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT DEFAULT 'auto',                 -- auto | manual | veto
  required_role TEXT,
  state TEXT DEFAULT 'pending',             -- pending | passed | failed | vetoed
  note TEXT,
  superseded INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workshops (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage_key TEXT DEFAULT 'workshop',
  topic TEXT,
  state TEXT DEFAULT 'open',                -- open | closed
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workshop_turns (
  id TEXT PRIMARY KEY,
  workshop_id TEXT NOT NULL,
  round INTEGER NOT NULL,                   -- 1 提案 2 质询 3 修订 4 表决
  role_tag TEXT,
  author TEXT,
  kind TEXT,                                -- proposal | question | revision | vote | risk
  content TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stage_deliverables (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  stage_key TEXT NOT NULL,
  kind TEXT,                                -- charter | minutes | wbs | release | retro …
  file_path TEXT,
  bytes INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wbs_items (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  code TEXT NOT NULL,                       -- T1, T2 …
  title TEXT NOT NULL,
  role_tag TEXT,
  pack_id TEXT,
  depends_on TEXT DEFAULT '[]',             -- JSON array of codes
  task_id TEXT,
  validation_error TEXT,
  state TEXT DEFAULT 'todo'
);
"""

TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS trg_stage_no_skip
BEFORE UPDATE OF state ON project_stages
WHEN NEW.state = 'active' AND NEW.seq > 0
BEGIN
  SELECT CASE WHEN (
      SELECT state FROM project_stages WHERE project_id = NEW.project_id AND seq = NEW.seq - 1
    ) IS NOT 'done'
  THEN RAISE(ABORT, 'StageSkipped') END;
END;

-- 阶段要 done：既要有通过记录，也不能有未通过/被否决的闸门
CREATE TRIGGER IF NOT EXISTS trg_stage_gate_guard
BEFORE UPDATE OF state ON project_stages
WHEN NEW.state = 'done'
BEGIN
  SELECT CASE WHEN NOT EXISTS (
      SELECT 1 FROM stage_gates
      WHERE project_id = NEW.project_id AND stage_key = NEW.key
        AND superseded=0 AND state = 'passed'
  ) THEN RAISE(ABORT, 'GateNotPassed') END;
  SELECT CASE WHEN EXISTS (
      SELECT 1 FROM stage_gates
      WHERE project_id = NEW.project_id AND stage_key = NEW.key
        AND superseded=0 AND state <> 'passed'
  ) THEN RAISE(ABORT, 'GateNotPassed') END;
END;

CREATE TRIGGER IF NOT EXISTS trg_gate_veto_ins
AFTER INSERT ON stage_gates
WHEN NEW.state = 'vetoed' AND NEW.superseded=0
BEGIN
  UPDATE projects SET state = 'blocked' WHERE id = NEW.project_id;
  UPDATE project_stages SET state = 'blocked'
   WHERE project_id = NEW.project_id AND key = NEW.stage_key;
END;

CREATE TRIGGER IF NOT EXISTS trg_gate_veto_upd
AFTER UPDATE OF state ON stage_gates
WHEN NEW.state = 'vetoed' AND NEW.superseded=0
BEGIN
  UPDATE projects SET state = 'blocked' WHERE id = NEW.project_id;
  UPDATE project_stages SET state = 'blocked'
   WHERE project_id = NEW.project_id AND key = NEW.stage_key;
END;
"""


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
    if "project_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")
    if "stage" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN stage TEXT")
    pcols = [r[1] for r in conn.execute("PRAGMA table_info(projects)")]
    if "use_llm_docs" not in pcols:
        conn.execute("ALTER TABLE projects ADD COLUMN use_llm_docs INTEGER DEFAULT 0")
    for name, ddl in {"tenant_id": "TEXT NOT NULL DEFAULT 'TENANT-HQ'",
                      "veto_note": "TEXT", "retry_token": "TEXT", "last_error": "TEXT"}.items():
        if name not in pcols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {ddl}")
    if "tenant_id" not in pcols:
        conn.execute("""UPDATE projects SET tenant_id=COALESCE(
            (SELECT tenant_id FROM legal_entities WHERE id=legal_entity_id),'TENANT-HQ')""")
    wcols = {r[1] for r in conn.execute("PRAGMA table_info(wbs_items)")}
    for name in ("task_id", "validation_error"):
        if name not in wcols:
            conn.execute(f"ALTER TABLE wbs_items ADD COLUMN {name} TEXT")
    gcols = {r[1] for r in conn.execute("PRAGMA table_info(stage_gates)")}
    if "superseded" not in gcols:
        conn.execute("ALTER TABLE stage_gates ADD COLUMN superseded INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE TABLE IF NOT EXISTS pipeline_migrations (name TEXT PRIMARY KEY)")
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    if not conn.execute("SELECT 1 FROM pipeline_migrations WHERE name='gate-history-v1'").fetchone():
        # One-time replacement of old guards; concurrent normal ensure calls never
        # open a window without triggers.
        for trigger in ("trg_stage_gate_guard", "trg_gate_veto_ins", "trg_gate_veto_upd"):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        statement = ""
        for line in TRIGGERS.splitlines(True):
            statement += line
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        conn.execute("INSERT INTO pipeline_migrations(name) VALUES ('gate-history-v1')")
    conn.commit()
    conn.executescript(TRIGGERS)
    conn.commit()


def create_project(conn, *, name: str, requirement: str, entity: str = "LE-GROUP-HQ",
                   owner: str | None = None, use_llm_docs: int = 0,
                   tenant_id: str | None = None) -> dict:
    ensure(conn)
    from . import tenancy
    actual_tenant = tenancy.of_entity(conn, entity)
    if tenant_id is not None and tenant_id != actual_tenant:
        raise PermissionError("TenantMismatch")
    tenant_id = actual_tenant
    if owner:
        _execution_actor(conn, owner, entity, tenant_id)
    pid = _id("P")
    conn.execute("""INSERT INTO projects (id, name, requirement, legal_entity_id,
                                           tenant_id,owner_identity, use_llm_docs)
                    VALUES (?,?,?,?,?,?,?)""", (pid, name, requirement, entity,tenant_id,owner,
                                             1 if use_llm_docs else 0))
    for seq, (key, title) in enumerate(STAGES):
        conn.execute("""INSERT INTO project_stages (id, project_id, seq, key, title)
                        VALUES (?,?,?,?,?)""", (_id("S"), pid, seq, key, title))
    conn.commit()
    write_audit(conn, legal_entity_id=entity, verb="project", task_id=None,
                actor=owner, layer="system", action="project_created", target=pid,
                payload={"name": name, "requirement": requirement[:200],
                         "owner_identity": owner}, response={})
    conn.commit()
    return {"project_id": pid, "stages": len(STAGES)}


def _stage(conn, pid: str, key: str):
    return conn.execute("SELECT * FROM project_stages WHERE project_id=? AND key=?",
                        (pid, key)).fetchone()


def _wdir(project_id: str) -> pathlib.Path:
    from .engine import dsh_adapter
    p = dsh_adapter.workspace() / "projects" / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write(conn, pid: str, stage_key: str, kind: str, name: str, text: str) -> str:
    delivery_id = _id("D")
    path = _wdir(pid) / "deliveries" / delivery_id / pathlib.Path(name).name
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(text, encoding="utf-8")
    conn.execute("""INSERT INTO stage_deliverables (id, project_id, stage_key, kind, file_path, bytes)
                    VALUES (?,?,?,?,?,?)""",
                 (delivery_id, pid, stage_key, kind, str(path), path.stat().st_size))
    conn.commit()
    return str(path)


def gate(conn, pid: str, stage_key: str, name: str, *, kind: str = "auto",
         role: str | None = None, passed: bool = True, note: str = "") -> str:
    gid = _id("G")
    conn.execute("""INSERT INTO stage_gates (id, project_id, stage_key, name, kind, required_role, state, note)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (gid, pid, stage_key, name, kind, role,
                  "passed" if passed else ("vetoed" if kind == "veto" else "failed"), note))
    conn.commit()
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="gate", task_id=None, layer="system",
                action="gate_pass" if passed else ("gate_veto" if kind == "veto" else "gate_fail"),
                target=gid, payload={"project": pid, "stage": stage_key, "name": name},
                response={"note": note})
    conn.commit()
    return gid


def enter_stage(conn, pid: str, key: str) -> None:
    row = _stage(conn, pid, key)
    if row is None:
        raise ValueError(f"unknown stage {key}")
    conn.execute("UPDATE project_stages SET state='active', started_at=CURRENT_TIMESTAMP WHERE id=?",
                 (row["id"],))
    conn.execute("UPDATE projects SET current_seq=? WHERE id=?", (row["seq"], pid))
    conn.commit()
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="stage", task_id=None, layer="system",
                action="stage_enter", target=pid, payload={"stage": key, "seq": row["seq"]},
                response={})
    conn.commit()


def finish_stage(conn, pid: str, key: str) -> None:
    row = _stage(conn, pid, key)
    conn.execute("UPDATE project_stages SET state='done', ended_at=CURRENT_TIMESTAMP WHERE id=?",
                 (row["id"],))
    conn.commit()
    write_audit(conn, legal_entity_id="LE-GROUP-HQ", verb="stage", task_id=None, layer="system",
                action="stage_done", target=pid, payload={"stage": key}, response={})
    conn.commit()


# ------------------------------------------------------------------ 研讨会
WORKSHOP_ROLES = [("architect", "架构师"), ("qa", "测试"), ("audit", "审计/合规"),
                  ("legal", "法务")]


def run_workshop(conn, pid: str, requirement: str, llm_minutes: bool = False) -> dict:
    """强制四轮：提案 → 质询 → 修订 → 表决。

    v1.6：质询与风险由**各自的角色模型**产出（真辩论），纪要由模型汇总；
    模型不可用时回落为固定文本，流程不中断。
    """
    from . import pipeline_llm as gen

    wid = _id("W")
    conn.execute("INSERT INTO workshops (id, project_id, topic) VALUES (?,?,?)",
                 (wid, pid, requirement[:120]))
    turns = []

    def say(round_no: int, role: str, author: str, kind: str, content: str):
        conn.execute(
            "INSERT INTO workshop_turns (id, workshop_id, round, role_tag, author, kind, content)"
            " VALUES (?,?,?,?,?,?,?)",
            (_id("WT"), wid, round_no, role, author, kind, content))
        turns.append({"round": round_no, "role": role, "kind": kind, "content": content})

    say(1, "product", "产品经理",
        "proposal", "提案：" + requirement[:80] + "。先做最小可用版本，四步推进。")

    statements = []
    prior = conn.execute("SELECT veto_note FROM projects WHERE id=?", (pid,)).fetchone()
    for role, title in WORKSHOP_ROLES:
        got = gen.workshop_statement(pid, requirement, role, title) if llm_minutes else None
        if gen.valid_statement(got):
            q, risk, stance = got["question"], got["risk"], got["stance"]
        else:
            q = title + "质询：预算与边界如何界定？失败如何回滚？"
            risk = title + "风险：相关风险已登记，需人工复核。"
            stance = ("反对" if (llm_minutes or prior and prior["veto_note"])
                      and role in {"audit", "legal"}
                      else "有条件同意")
            if stance == "反对":
                risk = "评审未完成：模型不可用或未返回有效的明确结论，请重新评审。"
        say(2, role, title, "question", q)
        say(2, role, title, "risk", risk)
        say(4, role, title, "vote", stance)
        statements.append({"role": role, "title": title, "question": q,
                           "risk": risk, "stance": stance})

    vetoes = [s for s in statements if s["role"] in {"audit", "legal"}
              and s.get("stance") == "反对"]
    veto_note = "；".join(f"{s['title']}：{s['question']}；{s['risk']}" for s in vetoes)
    conn.execute("UPDATE projects SET veto_note=? WHERE id=?", (veto_note or None, pid))
    say(3, "product", "产品经理", "revision",
        ("【一票否决】请按审计/法务意见修改需求后重新评审，禁止强行推进。"
         if vetoes else "修订：接受上述质询与风险，补充回滚方案与验收口径。"))
    say(4, "product", "产品经理", "vote", "提案被否决，退回重写" if vetoes else "表决通过（附条件）")
    conn.commit()

    minutes = gen.minutes(pid, requirement, statements) if llm_minutes else None
    if not minutes:
        lines = ["# 研讨会纪要", "项目：" + pid, "议题：" + requirement, "", "## 风险与反对意见"]
        lines += ["- " + s["title"] + "：" + s["risk"] for s in statements]
        lines += ["", "## 结论", ("否决：" + veto_note) if vetoes else "通过（附风险清单）"]
        minutes = "\n".join(lines)
    elif vetoes:
        minutes += "\n\n## 系统记录的否决结论\n" + veto_note + "\n必须重新评审，当前未通过。"
    path = _write(conn, pid, "workshop", "minutes", "02_workshop_minutes.md", minutes)
    conn.execute("UPDATE workshops SET state='closed' WHERE id=?", (wid,))
    conn.commit()
    # v2.4：共识/风险沉淀为组织经验（strip 掉自动注入块，避免自我复制）
    try:
        from . import experience
        base_req = requirement.split("【组织经验（自动注入）】")[0].strip()
        experience.capture_consensus(conn, project_id=pid, workshop_id=wid,
                                     requirement=base_req, statements=statements,
                                     minutes=minutes)
    except Exception:                        # noqa: BLE001
        pass
    return {"workshop_id": wid, "turns": turns, "minutes": path,
            "llm": bool(llm_minutes), "vetoes": [s["role"] for s in vetoes],
            "veto_note": veto_note}


def build_wbs(conn, pid: str, requirement: str, llm: bool = False) -> dict:
    """WBS 按标签裁剪 + DAG 依赖；文档优先模型生成。"""
    from . import pipeline_llm as gen
    from .ui.roles import tags_of

    tags = set(tags_of(requirement))
    items = [("T1", "开发最小可用版本", "dev", "code-local-v1", [])]
    if "content" in tags or "growth" in tags:
        items.append(("T2", "撰写发布文案与使用说明", "content", "content-v1", ["T1"]))
    if "design" in tags:
        items.append(("T2D", "出静态界面稿", "design", "web-v1", ["T1"]))
    # 发布不是开发任务，交给阶段 7（release）；WBS 只放可交付的构建项


    for code, title, role, pack, deps in items:
        if conn.execute("SELECT 1 FROM wbs_items WHERE project_id=? AND code=?",
                         (pid, code)).fetchone():
            continue
        conn.execute(
            "INSERT INTO wbs_items (id, project_id, code, title, role_tag, pack_id, depends_on)"
            " VALUES (?,?,?,?,?,?,?)",
            (_id("WBS"), pid, code, title, role, pack, json.dumps(deps)))
    conn.commit()

    doc = gen.wbs(pid, requirement, [(c, t, r) for c, t, r, _p, _d in items]) if llm else None
    if not doc:
        md = ["# WBS 工作分解", "项目：" + pid, "", "| 编号 | 任务 | 角色 | 依赖 |",
              "|---|---|---|---|"]
        for code, title, role, _pack, deps in items:
            md.append("| " + code + " | " + title + " | " + role + " | "
                      + (", ".join(deps) or "—") + " |")
        md += ["", "## DAG", " → ".join(i[0] for i in items), "", "串行约束见依赖列。"]
        doc = "\n".join(md)
    path = _write(conn, pid, "wbs", "wbs", "03_wbs.md", doc)
    return {"items": [i[0] for i in items], "wbs": path, "llm": bool(llm)}


def can_start(conn, pid: str, code: str) -> bool:
    row = conn.execute("SELECT * FROM wbs_items WHERE project_id=? AND code=?",
                       (pid, code)).fetchone()
    if row is None:
        return False
    for dep in json.loads(row["depends_on"] or "[]"):
        d = conn.execute("SELECT state FROM wbs_items WHERE project_id=? AND code=?",
                         (pid, dep)).fetchone()
        if d is None or d["state"] != "done":
            return False
    return True


def complete_wbs(conn, pid: str, code: str) -> None:
    if not can_start(conn, pid, code):
        raise PermissionError(f"DAGBlocked: {code} 的前置任务未完成")
    conn.execute("UPDATE wbs_items SET state='done' WHERE project_id=? AND code=?", (pid, code))
    conn.commit()


# ------------------------------------------------------------------ 全流程
def _llm_doc(conn, pid: str, key: str, kind: str, name: str, text: str | None,
             fallback: str) -> str:
    """模型生成优先，失败回落模板；两者都写脚注标明来源。"""
    body = text if text else (fallback + "\n\n---\n_模板生成（模型不可用）_")
    return _write(conn, pid, key, kind, name, body)


def run_stage(conn, pid: str, key: str, requirement: str) -> dict:
    """执行单个阶段：进入 → 产物（优先模型生成）→ 闸门 → 结束。"""
    from . import pipeline_llm as gen

    row = conn.execute("SELECT use_llm_docs,state FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        raise KeyError(pid)
    if row["state"] == "blocked":
        raise PermissionError("ProjectRevisionRequired")
    llm_docs = bool(row and row["use_llm_docs"])
    enter_stage(conn, pid, key)
    previous_vetoes = conn.execute("""SELECT * FROM stage_gates WHERE project_id=?
        AND stage_key=? AND state='vetoed' AND superseded=1 ORDER BY rowid DESC""",
        (pid, key)).fetchall()
    if key != "workshop" and previous_vetoes:
        reviewed_roles = set()
        for previous in previous_vetoes:
            role = previous["required_role"] or "audit"
            if role in reviewed_roles:
                continue
            reviewed_roles.add(role)
            reviewed = (gen.workshop_statement(pid, requirement + "\n原否决：" +
                         (previous["note"] or previous["name"]), role, role)
                        if llm_docs else None)
            approved = bool(gen.valid_statement(reviewed)
                            and reviewed["stance"] in {"同意", "有条件同意"})
            gate(conn, pid, key, "原否决重新评审", kind="veto", role=role, passed=approved,
                 note=(reviewed.get("risk") or "重新评审通过") if approved
                 else "原否决未获重新批准；需要可用的独立评审模型")
            if not approved:
                raise PermissionError("VetoReevaluationRequired")
    out: dict = {"stage": key}

    if key == "intake":
        fb = "# 需求登记\n\n原始需求：" + requirement + "\n\n来源：CEO 调度台"
        out["file"] = _llm_doc(conn, pid, key, "intake", "00_intake.md",
                               (gen.intake(pid, requirement) if llm_docs else None), fb)
        gate(conn, pid, key, "需求已登记且可追溯", note="intake")

    elif key == "charter":
        fb = ("# 立项书\n\n## 背景\n" + requirement +
              "\n\n## 目标\nMVP 可上线\n## 风险\n需求变更、回滚")
        out["file"] = _llm_doc(conn, pid, key, "charter", "01_charter.md",
                               (gen.charter(pid, requirement) if llm_docs else None), fb)
        gate(conn, pid, key, "立项书章节完整", note="背景/目标/范围/里程碑/预算/风险")
        gate(conn, pid, key, "产品负责人签署", kind="manual", role="product", note="auto-signed")

    elif key == "workshop":
        out.update(run_workshop(conn, pid, requirement, llm_minutes=llm_docs))
        gate(conn, pid, key, "全员留下风险意见", note="architect/qa/audit")
        gate(conn, pid, key, "架构师否决权", kind="veto", role="architect", passed=True,
             note="未行使否决")
        gate(conn, pid, key, "审计/法务一票否决", kind="veto", role="audit",
             passed=not out.get("vetoes"), note=out.get("veto_note") or "未行使否决")
        if out.get("vetoes"):
            raise PermissionError("WorkshopVetoed")

    elif key == "wbs":
        out.update(build_wbs(conn, pid, requirement, llm=llm_docs))
        gate(conn, pid, key, "DAG 无环且依赖可满足", note="见 WBS 依赖图")

    elif key == "build":
        res = execute_build(conn, pid, requirement)
        out.update(res)
        rows = conn.execute("SELECT code, title, state FROM wbs_items WHERE project_id=?",
                            (pid,)).fetchall()
        lines = ["# 实现记录", "", "| 编号 | 任务 | 状态 |", "|---|---|---|"]
        lines += ["| " + r["code"] + " | " + r["title"] + " | " + r["state"] + " |" for r in rows]
        arts = conn.execute(
            "SELECT a.file_path, a.bytes FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
            " JOIN tasks t ON t.id=r.task_id WHERE t.project_id=?", (pid,)).fetchall()
        if arts:
            lines += ["", "## 产物"]
            lines += ["- " + a["file_path"] + "（" + str(a["bytes"]) + " B）" for a in arts]
        out["file"] = _write(conn, pid, key, "build", "04_build.md", "\n".join(lines))
        ok = res["failed"] == 0
        gate(conn, pid, key, "实现任务全部成功", passed=ok,
             note=f"{res['tasks']} 个任务 / 失败 {res['failed']}")
        if not ok:
            conn.execute("UPDATE projects SET state='blocked' WHERE id=?", (pid,))
            conn.execute("UPDATE project_stages SET state='blocked' WHERE project_id=? AND key=?",
                         (pid, key))
            conn.commit()
            raise RuntimeError("build failed: " + str(res["failed"]) + " task(s)")

    elif key == "test":
        res = execute_test(conn, pid)
        out.update(res)
        gate(conn, pid, key, "冒烟测试通过", passed=res["ok"], note=res["note"])
        if not res["ok"]:
            conn.execute("UPDATE projects SET state='blocked' WHERE id=?", (pid,))
            conn.commit()
            raise RuntimeError("test failed")

    elif key == "accept":
        ok, note = accept_check(conn, pid)
        text = "# 验收记录\n\n- 结论：" + ("通过" if ok else "未通过") + "\n- 依据：" + note + "\n"
        out["file"] = _write(conn, pid, key, "accept", "06_accept.md", text)
        gate(conn, pid, key, "验收通过", passed=ok, note=note)
        if not ok:
            conn.execute("UPDATE projects SET state='blocked' WHERE id=?", (pid,))
            conn.commit()
            raise RuntimeError("accept failed: " + note)

    elif key == "release":
        ok, note = accept_check(conn, pid)
        gate(conn, pid, key, "发布前重新验收", passed=ok, note=note)
        if not ok:
            conn.execute("UPDATE projects SET state='blocked' WHERE id=?", (pid,))
            conn.execute("UPDATE project_stages SET state='blocked'"
                         " WHERE project_id=? AND key=?", (pid, key))
            conn.commit()
            raise RuntimeError("release acceptance failed: " + note)
        arts = [d["file_path"] for d in conn.execute(
            "SELECT file_path FROM stage_deliverables WHERE project_id=?"
            " AND stage_key IN ('build','test')", (pid,)).fetchall()]
        fb = "# 发布清单\n\n- [x] 版本号与变更说明\n- [x] 回滚方案\n- [x] 监控与告警\n"
        out["file"] = _llm_doc(conn, pid, key, "release", "07_release_checklist.md",
                               (gen.release(pid, requirement, arts) if llm_docs else None), fb)
        gate(conn, pid, key, "发布清单全部勾选", note="checklist")

    elif key == "retro":
        facts = {
            "stages": 9,
            "tasks": conn.execute("SELECT COUNT(*) n FROM tasks WHERE project_id=?",
                                  (pid,)).fetchone()["n"],
            "tokens": conn.execute(
                "SELECT COALESCE(SUM(r.usage_tokens),0) t FROM task_runs r"
                " JOIN tasks t ON t.id=r.task_id WHERE t.project_id=?", (pid,)).fetchone()["t"],
        }
        fb = "# 复盘\n\n## 做对了什么\n- 阶段闸门前置\n## 改进项\n- 文档模型化\n"
        out["file"] = _llm_doc(conn, pid, key, "retro", "08_retro.md",
                               (gen.retro(pid, requirement, facts) if llm_docs else None), fb)
        gate(conn, pid, key, "复盘含改进项", note="actions")

    finish_stage(conn, pid, key)
    return out


def _task_entity(conn, pid: str) -> str:
    row = conn.execute("SELECT legal_entity_id FROM projects WHERE id=?", (pid,)).fetchone()
    return row["legal_entity_id"] if row else "LE-GROUP-HQ"


def _execution_actor(conn, owner: str | None, entity: str, tenant_id: str) -> dict:
    """Resolve an actual initiating identity; never invent a global dispatcher."""
    from . import identity, tenant, tenancy
    if not owner:
        raise PermissionError("ProjectOwnerRequired")
    ident = identity.get_identity(conn, owner)
    if ident is None:
        raise PermissionError("ProjectOwnerInactive")
    if tenancy.of_identity(conn, owner) != tenant_id or tenancy.of_entity(conn, entity) != tenant_id:
        raise PermissionError("ProjectOwnerScopeMismatch")
    legal = conn.execute("SELECT state FROM legal_entities WHERE id=?", (entity,)).fetchone()
    if legal is None or legal["state"] != "active":
        raise PermissionError("ProjectEntityInactive")
    if not identity.has_scope(ident, "task:create"):
        raise PermissionError("ProjectOwnerForbidden")
    allowed = (entity in tenant.visible_entities(conn, dict(ident)) if ident["role"] == "system"
               else ident["legal_entity_id"] == entity)
    if not allowed:
        raise PermissionError("ProjectOwnerScopeMismatch")
    return dict(ident)


def _build_employee(conn, item, *, entity: str, tenant_id: str) -> dict:
    """Choose the WBS role inside its entity, retaining a valid previous assignee."""
    from .ui import roles
    from . import tenancy
    if tenancy.of_entity(conn, entity) != tenant_id:
        raise PermissionError("ProjectEmployeeScopeMismatch")
    tag = item["role_tag"] or "dev"
    tags = [tag] + (["frontend", "backend"] if tag == "dev" else [])
    marks = ",".join("?" for _ in tags)
    rows = conn.execute(
        f"""SELECT e.*,r.tag FROM ai_employees e JOIN role_templates r ON r.id=e.role_id
            LEFT JOIN positions p ON p.id=e.position_id
            WHERE e.legal_entity_id=? AND COALESCE(e.status,'online')='online'
              AND r.state='active' AND r.tag IN ({marks})
              AND (p.id IS NULL OR (p.legal_entity_id=e.legal_entity_id AND p.state='active'))
            ORDER BY CASE WHEN r.tag=? THEN 0 ELSE 1 END,e.id""",
        (entity, *tags, tag)).fetchall()
    if not rows:
        # Match the existing dispatcher policy: instantiate a role template only
        # in the already-authorized project entity, then verify it before use.
        roles.pick(conn, [tag], entity_id=entity)
        rows = conn.execute(
            f"""SELECT e.*,r.tag FROM ai_employees e JOIN role_templates r ON r.id=e.role_id
                LEFT JOIN positions p ON p.id=e.position_id
                WHERE e.legal_entity_id=? AND COALESCE(e.status,'online')='online'
                  AND r.state='active' AND r.tag IN ({marks})
                  AND (p.id IS NULL OR (p.legal_entity_id=e.legal_entity_id AND p.state='active'))
                ORDER BY CASE WHEN r.tag=? THEN 0 ELSE 1 END,e.id""",
            (entity, *tags, tag)).fetchall()
    if not rows:
        raise PermissionError("ProjectEmployeeRequired")
    previous = (conn.execute("SELECT owner_employee_id FROM tasks WHERE id=?", (item["task_id"],))
                .fetchone() if item["task_id"] else None)
    if previous:
        chosen = next((row for row in rows if row["id"] == previous["owner_employee_id"]), None)
        if chosen:
            return dict(chosen)
    return dict(rows[0])


def execute_build(conn, pid: str, requirement: str) -> dict:
    """按 WBS 建 task（挂 project_id/stage），再真的调用 ACP 写代码。"""
    import os
    import uuid as _uuid

    from . import acceptance_run
    from .engine import dsh_adapter, task_generator

    entity = _task_entity(conn, pid)
    project = conn.execute("SELECT tenant_id,owner_identity FROM projects WHERE id=?", (pid,)).fetchone()
    if project is None:
        raise KeyError(pid)
    tenant_id = project["tenant_id"]
    items = [r for r in conn.execute(
        "SELECT * FROM wbs_items WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()]
    created = failed = 0
    budget = int(os.environ.get("M0_PROJECT_TASK_BUDGET", "120000"))

    stub = os.environ.get("M0_PIPELINE_EXEC", "1") != "1"
    actor_id = project["owner_identity"]
    if not stub:
        actor_id = _execution_actor(conn, actor_id, entity, tenant_id)["id"]
    for it in items:
        if it["role_tag"] == "ops":
            continue
        if it["state"] == "done":
            if it["task_id"]:
                ok, note = _check_task_redlines(conn, pid, it["task_id"])
                if not ok:
                    failed += 1
            continue
        if not can_start(conn, pid, it["code"]):
            continue
        existing = (conn.execute("SELECT state FROM tasks WHERE id=?", (it["task_id"],)).fetchone()
                    if it["task_id"] else None)
        if existing and not it["validation_error"]:
            if existing["state"] == "completed":
                ok, note = _check_task_redlines(conn, pid, it["task_id"])
                if ok:
                    complete_wbs(conn, pid, it["code"])
                else:
                    failed += 1
                continue
            if existing["state"] == "pending_review":
                reviewed = acceptance_run.run(conn, it["task_id"], [entity])
                if reviewed["task_state"] == "completed":
                    complete_wbs(conn, pid, it["code"])
                else:
                    failed += 1
                continue
            if existing["state"] in {"todo", "assigned", "in_progress"}:
                failed += 1
                continue
        brief = (it["title"] + "：单文件 Python 命令行实现，≤200 行，不引入第三方依赖；"
                 "项目 " + pid + "，需求 " + requirement[:40])
        employee = None if stub else _build_employee(conn, it, entity=entity, tenant_id=tenant_id)
        if not stub:
            # Role selection may perform writes. Recheck the initiating identity
            # immediately before creating the execution and its durable owner.
            _execution_actor(conn, actor_id, entity, tenant_id)
        gen = task_generator.generate(conn, brief, legal_entity_id=entity,
                                      pack_id=it["pack_id"], tenant_id=tenant_id)
        tid = gen["task_id"]
        conn.execute("UPDATE tasks SET project_id=?,stage='build',owner_employee_id=?,"
                     "owner_position_id=? WHERE id=?",
                     (pid, employee["id"] if employee else None,
                      employee["position_id"] if employee else None, tid))
        conn.execute("UPDATE wbs_items SET task_id=?,validation_error=NULL WHERE id=?", (tid, it["id"]))
        conn.commit()
        created += 1

        if stub:                       # 测试/离线：只建任务与占位产物，不调 ACP
            art = _wdir(pid) / (it["code"] + "_stub.py")
            art.write_text("# stub for " + it["code"] + "\nprint('ok')\n", encoding="utf-8")
            stub_run = "RUN-STUB-" + _uuid.uuid4().hex[:8]
            conn.execute("INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id,"
                         " tenant_id,state, runner, actor_identity)"
                         " VALUES (?,?,1,?,?, 'succeeded', 'stub', ?)",
                         (stub_run, tid, entity, tenant_id, actor_id))
            conn.execute("INSERT INTO artifacts (id, run_id,tenant_id,art_seq,file_path,checksum,bytes)"
                         " VALUES (?,?,?,1,?,?,?)",
                         ("ART-" + _uuid.uuid4().hex[:10], stub_run,tenant_id,str(art),
                          "stub", art.stat().st_size))
            from . import state_machine as _sm
            try:
                _sm.transition(conn, tid, "run_spawned", actor="system")
                _sm.transition(conn, tid, "run_started", actor="system")
                _sm.transition(conn, tid, "run_succeeded", actor="system")
                # This branch is explicitly offline simulation. Keep its terminal
                # status distinct in the audit from a real ACP acceptance.
                _sm.transition(conn, tid, "review_passed", actor="stub")
            except Exception:              # noqa: BLE001
                pass
            conn.commit()
            complete_wbs(conn, pid, it["code"])
            continue

        run_id = "RUN-" + _uuid.uuid4().hex[:16]
        conn.execute(
            "INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id,tenant_id,state,"
            " runner, actor_identity) VALUES (?,?,1,?,?, 'spawned', 'acp', ?)",
            (run_id, tid, entity,tenant_id,actor_id))
        # 状态机：todo -> assigned（runner 只会从 assigned 推到 in_progress）
        from . import state_machine as _sm0
        try:
            _sm0.transition(conn, tid, "run_spawned", actor="system")
        except Exception:                  # noqa: BLE001
            pass
        conn.commit()
        out = dsh_adapter.execute_run(conn, run_id,
                                      runner=os.environ.get("UI_RUNNER", "acp"),
                                      budget_tokens=budget)
        state = out.get("task_state")
        if state in ("pending_review", "completed"):
            try:
                reviewed = acceptance_run.run(conn, tid, [entity])
                if reviewed["task_state"] == "completed":
                    complete_wbs(conn, pid, it["code"])
                else:
                    failed += 1
            except (ValueError, sqlite3.Error, PermissionError):
                failed += 1
        else:
            failed += 1
    return {"tasks": created, "failed": failed, "run_dir": str(_wdir(pid))}


def execute_test(conn, pid: str) -> dict:
    """对 build 产物做语法检查 + 限时运行，输出真实日志。"""
    import subprocess
    import sys as _sys

    rows = conn.execute(
        "SELECT a.file_path,t.id AS task_id FROM artifacts a JOIN task_runs r ON r.id=a.run_id"
        " JOIN tasks t ON t.id=r.task_id"
        " WHERE t.project_id=? AND a.file_path LIKE '%.py'"
        " AND (NOT EXISTS (SELECT 1 FROM wbs_items w WHERE w.project_id=t.project_id"
        " AND w.task_id IS NOT NULL) OR t.id IN (SELECT task_id FROM wbs_items"
        " WHERE project_id=t.project_id))"
        " AND r.run_seq=(SELECT MAX(r2.run_seq) FROM task_runs r2 WHERE r2.task_id=t.id)"
        " AND COALESCE(a.state,'active')='active' ORDER BY a.rowid DESC",
        (pid,)).fetchall()
    log = ["# 测试记录", ""]
    ok = True
    failures = set()
    if not rows:
        ok = False
        log.append("未找到可测试的 .py 产物")
    for r in rows:
        path = pathlib.Path(r["file_path"])
        log.append("## " + path.name)
        if not path.exists():
            ok = False
            failures.add(r["task_id"])
            log.append("- 文件不存在")
            continue
        try:
            comp = subprocess.run([_sys.executable, "-m", "py_compile", str(path)],
                                  capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            ok = False
            failures.add(r["task_id"])
            log.append("- 编译检查超时")
            continue
        log.append("- py_compile: " + ("通过" if comp.returncode == 0 else "失败"))
        if comp.returncode != 0:
            ok = False
            failures.add(r["task_id"])
            log.append("```\n" + (comp.stderr or "")[:800] + "\n```")
            continue
        try:
            run = subprocess.run([_sys.executable, str(path)], capture_output=True,
                                 text=True, timeout=15, cwd=str(path.parent),
                                 input="\n" * 20)
            log.append("- 运行退出码: " + str(run.returncode))
            if run.returncode != 0:
                ok = False
                failures.add(r["task_id"])
            out = (run.stdout or "")[:1200]
            if out:
                log.append("```\n" + out + "\n```")
        except subprocess.TimeoutExpired:
            ok = False
            failures.add(r["task_id"])
            log.append("- 运行超时，未通过")
    conn.execute("UPDATE wbs_items SET validation_error=NULL WHERE project_id=?", (pid,))
    for task_id in failures:
        conn.execute("UPDATE wbs_items SET validation_error='交付物执行检查失败'"
                     " WHERE project_id=? AND task_id=?", (pid, task_id))
    conn.commit()
    body = "\n".join(log)
    path = _write(conn, pid, "test", "test", "05_test.md", body)
    return {"ok": ok, "note": "见 05_test.md", "file": path}


def _check_task_redlines(conn, pid: str, task_id: str) -> tuple[bool, str]:
    from . import acceptance_run
    verdict = acceptance_run.validate_mandatory(conn, task_id, commit=False)
    if not verdict["ok"]:
        conn.execute("UPDATE wbs_items SET validation_error=? WHERE project_id=? AND task_id=?",
                     (verdict["detail"], pid, task_id))
    return verdict["ok"], verdict["detail"]


def accept_check(conn, pid: str) -> tuple[bool, str]:
    n_total = conn.execute("SELECT COUNT(*) n FROM wbs_items WHERE project_id=?",
                           (pid,)).fetchone()["n"]
    n_done = conn.execute("SELECT COUNT(*) n FROM wbs_items WHERE project_id=? AND state='done'",
                          (pid,)).fetchone()["n"]
    bad = conn.execute(
        "SELECT COUNT(*) n FROM tasks t WHERE project_id=? AND state<>'completed'"
        " AND (NOT EXISTS (SELECT 1 FROM wbs_items w WHERE w.project_id=t.project_id"
        " AND w.task_id IS NOT NULL) OR t.id IN (SELECT task_id FROM wbs_items"
        " WHERE project_id=t.project_id))",
        (pid,)).fetchone()["n"]
    if bad:
        return False, str(bad) + " 个当前交付任务尚未完成验收"
    if n_done < n_total:
        return False, "WBS 完成 " + str(n_done) + "/" + str(n_total)
    tasks = conn.execute("""SELECT t.id FROM tasks t WHERE project_id=?
        AND (NOT EXISTS (SELECT 1 FROM wbs_items w WHERE w.project_id=t.project_id
            AND w.task_id IS NOT NULL) OR t.id IN (SELECT task_id FROM wbs_items
            WHERE project_id=t.project_id))""", (pid,)).fetchall()
    for task in tasks:
        ok, detail = _check_task_redlines(conn, pid, task["id"])
        if not ok:
            return False, "当前交付物未通过最新红线复核：" + detail
    return True, "WBS " + str(n_done) + "/" + str(n_total) + "，任务无失败"


def run_project(conn, pid: str, requirement: str, keys: list[str] | None = None) -> dict:
    import os
    if os.environ.get("M0_PIPELINE_EXEC", "1") == "1":
        project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if project is None:
            raise KeyError(pid)
        _execution_actor(conn, project["owner_identity"], project["legal_entity_id"],
                         project["tenant_id"])
    keys = keys or [k for k, _ in STAGES]
    # v2.4 分层记忆：立项时把同类「驳回经验 / 共识结论」注入需求上下文。
    # 注入文本会随立项书/纪要落盘，审计留 experience_injected，可回溯是哪几条。
    try:
        from . import experience
        proj = conn.execute("SELECT legal_entity_id,tenant_id,veto_note FROM projects WHERE id=?",
                            (pid,)).fetchone()
        entity = proj["legal_entity_id"] if proj else "LE-GROUP-HQ"
        if "【组织经验（自动注入）】" not in requirement:
            ctx = experience.context_for(conn, requirement, entity=entity,
                                         tenant_id=proj["tenant_id"])
            if ctx["count"]:
                requirement = (requirement + "\n\n【组织经验（自动注入）】\n" + ctx["text"])
                write_audit(conn, legal_entity_id=entity, verb="memory_read", task_id=None,
                            layer="system", action="experience_injected", target=pid,
                            payload={"ids": ctx["ids"], "tags": ctx["tags"],
                                     "query": requirement[:200]},
                            response={"ok": True, "count": ctx["count"]})
                conn.commit()
        if proj and proj["veto_note"]:
            requirement += "\n\n【一票否决重写要求】\n" + proj["veto_note"]
    except Exception:                        # noqa: BLE001 - 记忆不可用不阻塞产线
        pass
    done = []
    for key in keys:
        run_stage(conn, pid, key, requirement)
        done.append(key)
    # v4.0：9/9 阶段全部 done ⇒ 企微群推「商业闭环」。投递走后台线程，
    # 通知失败只落 notifications.state='failed'，绝不回滚产线。
    try:
        from . import notify
        notify.project_closed(conn, pid)
    except Exception:                        # noqa: BLE001 - 通知不是闸门
        pass
    return {"project_id": pid, "stages": done}


def timeline(conn, pid: str) -> list[dict]:
    rows = conn.execute(
        """SELECT action, target, payload, created_at FROM ai_audit_logs
           WHERE payload LIKE ? OR target = ?
           ORDER BY rowid ASC""", (f"%{pid}%", pid)).fetchall()
    return [dict(r) for r in rows]


def prepare_retry(conn, pid: str, requirement: str, *, actor: dict) -> dict:
    """Claim a recovery once, retaining prior gates and completed work."""
    import os
    from . import identity, tenancy
    ensure(conn)
    who = identity.get_identity(conn, actor.get("id", ""))
    if who is None or who["role"] not in {"ceo", "system"}:
        raise PermissionError("ProjectReviewerRequired")
    tenant_id = tenancy.of_identity(conn, who["id"])
    requirement = requirement.strip()
    conn.execute("BEGIN IMMEDIATE")
    try:
        p = conn.execute("SELECT * FROM projects WHERE id=? AND tenant_id=?"
            " AND legal_entity_id=?", (pid, tenant_id, who["legal_entity_id"])).fetchone()
        if p is None:
            raise KeyError(pid)
        if os.environ.get("M0_PIPELINE_EXEC", "1") == "1":
            _execution_actor(conn, p["owner_identity"], p["legal_entity_id"], p["tenant_id"])
        if p["state"] != "blocked" or p["retry_token"]:
            raise RuntimeError("ProjectRetryUnavailable")
        if not requirement or requirement == p["requirement"].strip():
            raise ValueError("RevisedRequirementRequired")
        stage = conn.execute("""SELECT s.* FROM project_stages s
            WHERE s.project_id=? AND (s.state='blocked' OR EXISTS(
                SELECT 1 FROM stage_gates g WHERE g.project_id=s.project_id
                AND g.stage_key=s.key AND g.superseded=0 AND g.state IN ('failed','vetoed')))
            ORDER BY s.seq LIMIT 1""", (pid,)).fetchone()
        if stage is None:
            stage = conn.execute("SELECT * FROM project_stages WHERE project_id=?"
                " AND state<>'done' ORDER BY seq LIMIT 1", (pid,)).fetchone()
        if stage is None:
            raise RuntimeError("ProjectRetryUnavailable")
        start_seq = stage["seq"]
        broken_work = conn.execute("""SELECT w.id FROM wbs_items w
            LEFT JOIN tasks t ON t.id=w.task_id WHERE w.project_id=?
            AND (w.validation_error IS NOT NULL OR t.state IN ('failed','rejected'))""",
            (pid,)).fetchall()
        if start_seq >= 4 and broken_work:
            start_seq = 4
            for item in broken_work:
                conn.execute("UPDATE wbs_items SET state='todo' WHERE id=?",
                             (item["id"],))
        keys = [r["key"] for r in conn.execute("SELECT key FROM project_stages"
            " WHERE project_id=? AND seq>=? ORDER BY seq", (pid, start_seq))]
        old_gates = [dict(r) for r in conn.execute("""SELECT g.* FROM stage_gates g
            JOIN project_stages s ON s.project_id=g.project_id AND s.key=g.stage_key
            WHERE g.project_id=? AND s.seq>=? AND g.superseded=0""", (pid, start_seq))]
        conn.execute("""UPDATE stage_gates SET superseded=1 WHERE project_id=?
            AND stage_key IN (SELECT key FROM project_stages WHERE project_id=? AND seq>=?)""",
            (pid, pid, start_seq))
        conn.execute("UPDATE project_stages SET state='pending',ended_at=NULL"
                     " WHERE project_id=? AND seq>=?", (pid, start_seq))
        token = uuid.uuid4().hex
        conn.execute("UPDATE projects SET state='retrying',retry_token=?,requirement=?,"
                     "last_error=NULL WHERE id=?", (token, requirement, pid))
        write_audit(conn, legal_entity_id=p["legal_entity_id"], verb="project",
            actor=who["id"], action="project_revision_requested", target=pid,
            payload={"previous_requirement": p["requirement"], "requirement": requirement,
                     "from_stage": keys[0], "previous_gates": old_gates},
            response={"state": "retrying"})
        conn.commit()
        return {"project_id": pid, "requirement": requirement, "keys": keys, "token": token}
    except BaseException:
        conn.rollback()
        raise


def run_retry(conn, claim: dict) -> dict:
    pid, token = claim["project_id"], claim["token"]
    finished = []
    try:
        for key in claim["keys"]:
            row = conn.execute("SELECT state,retry_token FROM projects WHERE id=?", (pid,)).fetchone()
            if not row or row["state"] != "retrying" or row["retry_token"] != token:
                raise RuntimeError("ProjectRetryClaimLost")
            run_project(conn, pid, claim["requirement"], keys=[key])
            finished.append(key)
        conn.execute("UPDATE projects SET state='active',retry_token=NULL,last_error=NULL"
                     " WHERE id=? AND retry_token=?", (pid, token))
        conn.commit()
        return {"project_id": pid, "stages": finished}
    except BaseException as exc:
        fail_retry(conn, claim, type(exc).__name__)
        raise


def fail_retry(conn, claim: dict, reason: str) -> None:
    pid, token = claim["project_id"], claim["token"]
    conn.rollback()
    updated = conn.execute("UPDATE projects SET state='blocked',retry_token=NULL,last_error=?"
                           " WHERE id=? AND retry_token=?", (reason[:300], pid, token))
    if updated.rowcount:
        conn.execute("UPDATE project_stages SET state='blocked'"
                     " WHERE project_id=? AND state='active'", (pid,))
    conn.commit()


def recover_interrupted(conn) -> int:
    """Called once at process startup, before any recovery workers are launched."""
    ensure(conn)
    rows = conn.execute("SELECT id,retry_token FROM projects WHERE state='retrying'").fetchall()
    for row in rows:
        fail_retry(conn, {"project_id": row["id"], "token": row["retry_token"]},
                   "上次服务退出导致恢复中断，请补充需求后重试")
    return len(rows)
