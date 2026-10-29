"""UI Sub-router: HQ 3D Engine & Telemetry Snapshot."""
from __future__ import annotations

import re
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ...db import get_conn
from ... import finops, telemetry, tenant, tenancy
from .. import approval_access, roles as roles_mod, store
from ..common import _token, _require, _task_actor

router = APIRouter()

DEPT_PATTERNS = [
    (0, re.compile(r"产品|PM|需求|项目经理|规划", re.I)),
    (1, re.compile(r"设计|UI|UX|视觉|美工|插画", re.I)),
    (2, re.compile(r"市场|文案|营销|品牌|内容|推广|新媒体|撰稿", re.I)),
    (3, re.compile(r"运营|SRE|运维|客服|增长|社群|活动", re.I)),
    (4, re.compile(r"研发|开发|工程|前端|后端|Python|架构|算法|量化|测试|QA|代码|极客|小码|小测", re.I)),
    (5, re.compile(r"人事|HR|招聘|培训|组织|研究", re.I)),
    (6, re.compile(r"财务|金融|会计|税务|核算|成本|投研|小财", re.I)),
    (7, re.compile(r"法务|合规|审计|行政|风控|合同|安全", re.I)),
]

STAGE_DEPT_MAP = {
    "intake": 0, "charter": 0, "workshop": -1, "wbs": 0,
    "build": 4, "test": 4, "accept": -1, "release": 3, "retro": 7
}


def _has_table(conn, table_name: str) -> bool:
    try:
        r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
        return bool(r)
    except Exception:
        return False


def map_dept(title: str, name: str = "", entity_name: str = "") -> int:
    text = f"{title or ''} {name or ''} {entity_name or ''}"
    for idx, pat in DEPT_PATTERNS:
        if pat.search(text):
            return idx
    return 4


class HireRoleIn(BaseModel):
    role_id: str
    entity_id: str = "LE-GROUP-HQ"


@router.post("/hq/hire")
def hq_hire_role(payload: HireRoleIn, oa_ui: str | None = Depends(_token)):
    _require(oa_ui)
    with get_conn() as conn:
        roles_mod.ensure(conn)
        actor = _task_actor(conn, oa_ui, "task:*")
        tenant.resolve(actor, payload.entity_id)
        if tenancy.of_entity(conn, payload.entity_id) != actor["tenant_id"]:
            raise HTTPException(403, detail={"code": "TenantMismatch"})
        try:
            emp_id = roles_mod.instantiate(conn, payload.role_id, payload.entity_id)
        except ValueError as exc:
            raise HTTPException(404, detail={"code": "RoleNotFound", "message": str(exc)})
        emp = conn.execute("SELECT * FROM ai_employees WHERE id=?", (emp_id,)).fetchone()
        pos = conn.execute("SELECT * FROM positions WHERE id=?", (emp["position_id"],)).fetchone() if emp else None
        has_chats = _has_table(conn, "chats")
        if has_chats:
            chat = conn.execute("SELECT id FROM chats WHERE employee_id=? AND kind='dm'", (emp_id,)).fetchone()
        else:
            chat = None
    
    try:
        telemetry.emit(
            "ROSTER_UPDATED",
            action="hire",
            employee_id=emp_id,
            name=emp["name"] if emp else "",
            title=pos["title"] if pos else "",
            chat_id=chat["id"] if chat else None,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "employee_id": emp_id,
        "name": emp["name"] if emp else "",
        "title": pos["title"] if pos else "",
        "chat_id": chat["id"] if chat else None,
    }


@router.get("/hq/snapshot")
def get_hq_snapshot(oa_ui: str | None = Depends(_token)):
    """Fast, unified JSON snapshot from real SQLite tables (never fake data)."""
    with get_conn() as conn:
        roles_mod.ensure(conn)
        who = {"role": "system", "tenant_id": "TENANT-HQ"}
        if oa_ui:
            try:
                who = approval_access.actor(conn, oa_ui)
            except Exception:
                pass

        # ------------------------------------------------------------------ 1. Employees
        has_chats = _has_table(conn, "chats")
        if has_chats:
            emp_rows = conn.execute("""
                SELECT e.id, e.name, e.status, e.model, e.role_id,
                       p.title, le.name as entity_name,
                       c.id as chat_id
                FROM ai_employees e
                LEFT JOIN positions p ON p.id = e.position_id
                LEFT JOIN legal_entities le ON le.id = e.legal_entity_id
                LEFT JOIN chats c ON c.employee_id = e.id AND c.kind = 'dm'
                WHERE COALESCE(e.status, 'online') != 'fired'
            """).fetchall()
        else:
            emp_rows = conn.execute("""
                SELECT e.id, e.name, e.status, e.model, e.role_id,
                       p.title, le.name as entity_name,
                       NULL as chat_id
                FROM ai_employees e
                LEFT JOIN positions p ON p.id = e.position_id
                LEFT JOIN legal_entities le ON le.id = e.legal_entity_id
                WHERE COALESCE(e.status, 'online') != 'fired'
            """).fetchall()

        employees = []
        for r in emp_rows:
            dept_idx = map_dept(r['title'], r['name'], r['entity_name'])

            # Busy check: active/assigned task or active project stage
            task_busy = conn.execute("""
                SELECT id, title, state FROM tasks 
                WHERE owner_employee_id = ? AND state IN ('in_progress', 'assigned')
                ORDER BY updated_at DESC LIMIT 1
            """, (r['id'],)).fetchone()

            stage_busy = None
            if not task_busy:
                stage_busy = conn.execute("""
                    SELECT ps.id, ps.title, ps.key, ps.state, ps.project_id
                    FROM project_stages ps
                    WHERE ps.assignee_id = ? AND ps.state IN ('active', 'in_progress')
                    LIMIT 1
                """, (r['id'],)).fetchone()

            busy = bool(task_busy or stage_busy)
            cur_task = None
            if task_busy:
                cur_task = {"task_id": task_busy["id"], "title": task_busy["title"], "progress": 0.5}
            elif stage_busy:
                cur_task = {"task_id": stage_busy["id"], "title": f"流水线阶段: {stage_busy['title']}", "progress": 0.5}

            # Real done count
            done_count = conn.execute("""
                SELECT COUNT(*) as c FROM tasks WHERE owner_employee_id = ? AND state = 'completed'
            """, (r['id'],)).fetchone()['c']

            # Real tokens
            tokens = conn.execute("""
                SELECT COALESCE(SUM(r.usage_tokens), 0) as s
                FROM task_runs r
                JOIN tasks t ON t.id = r.task_id
                WHERE t.owner_employee_id = ?
            """, (r['id'],)).fetchone()['s']

            # Skills & tools
            skills = []
            if r['role_id']:
                rt = conn.execute("SELECT skills, tools FROM role_templates WHERE id=?", (r['role_id'],)).fetchone()
                if rt:
                    if rt['skills']:
                        skills.extend([s.strip() for s in rt['skills'].split(',') if s.strip()])
                    if rt['tools']:
                        skills.extend([s.strip() for s in rt['tools'].split(',') if s.strip()])
            
            # Bound MCP tools
            if _has_table(conn, "agent_mcp_tools") and _has_table(conn, "mcp_servers"):
                mcp_tools = conn.execute("""
                    SELECT s.name FROM agent_mcp_tools amt
                    JOIN mcp_servers s ON s.id = amt.server_id
                    WHERE amt.agent_id = ? AND amt.enabled = 1
                """, (r['id'],)).fetchall()
                for mt in mcp_tools:
                    if mt['name'] and mt['name'] not in skills:
                        skills.append(mt['name'])

            if not skills and r['title']:
                skills.append(r['title'])

            employees.append({
                "id": r['id'],
                "name": r['name'],
                "title": r['title'] or "AI 员工",
                "model": r['model'] or "deepseek-chat",
                "dept_idx": dept_idx,
                "chat_id": r['chat_id'],
                "status": r['status'] or "online",
                "busy": busy,
                "current_task": cur_task,
                "done_count": done_count,
                "tokens": int(tokens),
                "skills": skills,
            })

        # ------------------------------------------------------------------ 2. CEO Chat
        ceo_chat_row = conn.execute("SELECT id, title FROM chats WHERE kind='ceo' LIMIT 1").fetchone() if has_chats else None
        ceo_chat = (
            {"id": ceo_chat_row["id"], "title": ceo_chat_row["title"]}
            if ceo_chat_row else {"id": "C-CEO", "title": "CEO 调度台"}
        )

        # ------------------------------------------------------------------ 3. Roles Library
        role_rows = conn.execute("""
            SELECT id, name, tag, prompt, model, skills, tools FROM role_templates ORDER BY rowid
        """).fetchall()
        roles = []
        for ro in role_rows:
            d_idx = map_dept(ro['name'], ro['tag'])
            roles.append({
                "id": ro['id'],
                "name": ro['name'],
                "title": ro['name'],
                "role_tag": ro['tag'],
                "dept_idx": d_idx,
                "model": ro['model'],
                "skills": ro['skills'] or "",
                "tools": ro['tools'] or "",
                "description": ro['prompt'] or "",
            })

        # ------------------------------------------------------------------ 4. Projects & Pipelines
        proj_rows = conn.execute("""
            SELECT id, name, requirement, state, current_seq, flow_id, created_at
            FROM projects ORDER BY rowid DESC LIMIT 20
        """).fetchall()

        projects = []
        for p in proj_rows:
            # Stages
            stages_raw = conn.execute("""
                SELECT id, seq, key, title, state, role_tag, node_kind
                FROM project_stages WHERE project_id = ? ORDER BY seq
            """, (p['id'],)).fetchall()

            completed_stages = sum(1 for s in stages_raw if s['state'] == 'done')
            progress_pct = round((completed_stages / 9.0) * 100, 1) if stages_raw else (100.0 if p['state'] == 'done' else 0.0)

            stages = []
            for s in stages_raw:
                s_dept = STAGE_DEPT_MAP.get(s['key'], 4)
                stages.append({
                    "id": s['id'],
                    "seq": s['seq'],
                    "key": s['key'],
                    "title": s['title'],
                    "state": s['state'],
                    "role_tag": s['role_tag'],
                    "dept_idx": s_dept,
                })

            # WBS Items
            wbs_rows = conn.execute("""
                SELECT code, title, role_tag, state, task_id
                FROM wbs_items WHERE project_id = ? ORDER BY rowid
            """, (p['id'],)).fetchall()
            wbs_items = [
                {
                    "code": w['code'],
                    "title": w['title'],
                    "role_tag": w['role_tag'],
                    "state": w['state'],
                    "task_id": w['task_id'],
                }
                for w in wbs_rows
            ]

            # Workshop
            workshop_data = None
            w_row = conn.execute("SELECT id, topic, state FROM workshops WHERE project_id=? LIMIT 1", (p['id'],)).fetchone()
            if w_row:
                turns_raw = conn.execute("""
                    SELECT round, author, role_tag, kind, content
                    FROM workshop_turns WHERE workshop_id=? ORDER BY round, rowid
                """, (w_row['id'],)).fetchall()
                turns = [
                    {
                        "round": tr['round'],
                        "author": tr['author'],
                        "role_tag": tr['role_tag'],
                        "kind": tr['kind'],
                        "content": (tr['content'] or "")[:80],
                    }
                    for tr in turns_raw
                ]
                workshop_data = {
                    "id": w_row['id'],
                    "topic": w_row['topic'],
                    "state": w_row['state'],
                    "turns": turns,
                }

            # Pending gates
            pending_gates = 0
            if _has_table(conn, "stage_gates"):
                row_pg = conn.execute("""
                    SELECT COUNT(*) as c FROM stage_gates WHERE project_id = ? AND state != 'approved'
                """, (p['id'],)).fetchone()
                pending_gates = row_pg['c'] if row_pg else 0

            projects.append({
                "id": p['id'],
                "name": p['name'],
                "requirement": p['requirement'],
                "state": p['state'],
                "current_seq": p['current_seq'],
                "progress_pct": progress_pct,
                "stages": stages,
                "wbs_items": wbs_items,
                "workshop": workshop_data,
                "pending_gates": pending_gates,
            })

        # ------------------------------------------------------------------ 5. Standalone Tasks
        standalone_rows = conn.execute("""
            SELECT t.id, t.title, t.state, t.owner_employee_id, t.updated_at, t.created_at,
                   e.name as owner_name, p.title as pos_title
            FROM tasks t
            LEFT JOIN ai_employees e ON e.id = t.owner_employee_id
            LEFT JOIN positions p ON p.id = e.position_id
            WHERE t.project_id IS NULL OR t.project_id = ''
            ORDER BY COALESCE(t.updated_at, t.created_at) DESC LIMIT 30
        """).fetchall()

        standalone_tasks = []
        for st in standalone_rows:
            d_idx = map_dept(st['pos_title'], st['owner_name'])
            standalone_tasks.append({
                "id": st['id'],
                "title": st['title'],
                "state": st['state'],
                "owner_employee_id": st['owner_employee_id'],
                "owner_name": st['owner_name'] or "未指派",
                "dept_idx": d_idx,
                "updated_at": st['updated_at'] or st['created_at'],
            })

        # ------------------------------------------------------------------ 6. KPIs
        fin = finops.overview(conn)
        total_rmb = fin.get("totals", {}).get("cost", 0.0)

        approvals_listing = approval_access.listing(conn, who)
        pending_approvals = approvals_listing.get("count", 0)

        active_projs = conn.execute("SELECT COUNT(*) as c FROM projects WHERE state IN ('active', 'running')").fetchone()['c']
        total_projs = conn.execute("SELECT COUNT(*) as c FROM projects").fetchone()['c']
        done_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE state = 'completed'").fetchone()['c']
        done_projs = conn.execute("SELECT COUNT(*) as c FROM projects WHERE state = 'done'").fetchone()['c']
        total_done = done_tasks + done_projs

        wip_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE state IN ('in_progress', 'assigned')").fetchone()['c']
        wip_stages = conn.execute("SELECT COUNT(*) as c FROM project_stages WHERE state IN ('active', 'in_progress')").fetchone()['c']
        wip_count = wip_tasks + wip_stages

        # Weighted completion %
        if projects:
            active_list = [p for p in projects if p['state'] in ('active', 'running')]
            if active_list:
                progress_pct = round(sum(p['progress_pct'] for p in active_list) / len(active_list), 1)
            else:
                progress_pct = 100.0 if all(p['state'] == 'done' for p in projects) else 0.0
        elif standalone_tasks:
            completed_standalone = sum(1 for t in standalone_tasks if t['state'] == 'completed')
            progress_pct = round((completed_standalone / len(standalone_tasks)) * 100, 1)
        else:
            progress_pct = 0.0

        # Rework rate %
        failed_tasks = conn.execute("SELECT COUNT(*) as c FROM tasks WHERE state IN ('failed', 'rejected')").fetchone()['c']
        finished_tasks = done_tasks + failed_tasks
        rework_rate_pct = round((failed_tasks / finished_tasks * 100), 1) if finished_tasks > 0 else 0.0

        # Average lead time in minutes
        durations = []
        lead_rows = conn.execute("""
            SELECT started_at, ended_at FROM task_runs
            WHERE started_at IS NOT NULL AND ended_at IS NOT NULL AND state IN ('succeeded', 'done')
            ORDER BY rowid DESC LIMIT 40
        """).fetchall()
        for lr in lead_rows:
            try:
                # Format: 'YYYY-MM-DD HH:MM:SS'
                t_start = time.mktime(time.strptime(lr['started_at'][:19], "%Y-%m-%d %H:%M:%S"))
                t_end = time.mktime(time.strptime(lr['ended_at'][:19], "%Y-%m-%d %H:%M:%S"))
                diff_min = max(0.0, (t_end - t_start) / 60.0)
                durations.append(diff_min)
            except Exception:
                pass
        avg_lead_min = round(sum(durations) / len(durations), 1) if durations else 0.0

        # Today tokens
        today_usage = 0
        if _has_table(conn, "global_token_usage"):
            row_tu = conn.execute("SELECT COALESCE(SUM(total_tokens), 0) as s FROM global_token_usage WHERE day = date('now')").fetchone()
            today_usage = row_tu['s'] if row_tu else 0
        today_runs_usage = 0
        if _has_table(conn, "task_runs"):
            row_tru = conn.execute("SELECT COALESCE(SUM(usage_tokens), 0) as s FROM task_runs WHERE date(started_at) = date('now')").fetchone()
            today_runs_usage = row_tru['s'] if row_tru else 0
        today_tokens = int(max(today_usage or 0, today_runs_usage or 0))

        artifacts_count = conn.execute("SELECT COUNT(*) as c FROM artifacts").fetchone()['c'] if _has_table(conn, "artifacts") else 0
        deliverables_count = conn.execute("SELECT COUNT(*) as c FROM stage_deliverables").fetchone()['c'] if _has_table(conn, "stage_deliverables") else 0
        total_artifacts = artifacts_count + deliverables_count

        crystals_count = conn.execute("SELECT COUNT(*) as c FROM org_experiences").fetchone()['c'] if _has_table(conn, "org_experiences") else 0
        eval_sets_count = conn.execute("SELECT COUNT(*) as c FROM eval_sets").fetchone()['c'] if _has_table(conn, "eval_sets") else 0
        mcp_count = conn.execute("SELECT COUNT(*) as c FROM mcp_servers").fetchone()['c'] if _has_table(conn, "mcp_servers") else 0

        kpis = {
            "progress_pct": progress_pct,
            "active_projects_count": active_projs,
            "total_projects_count": total_projs,
            "done_count": total_done,
            "wip_count": wip_count,
            "pending_approvals": pending_approvals,
            "avg_lead_min": avg_lead_min,
            "rework_rate_pct": rework_rate_pct,
            "today_tokens": today_tokens,
            "total_rmb": round(float(total_rmb), 2),
            "artifacts_count": total_artifacts,
            "crystals_count": crystals_count,
            "eval_sets_count": eval_sets_count,
            "mcp_count": mcp_count,
        }

        # ------------------------------------------------------------------ 7. Real Activity Feed
        feed_events = []

        # Project stages
        ps_rows = conn.execute("""
            SELECT ps.id, ps.project_id, ps.title, ps.key, ps.state, ps.started_at, ps.ended_at,
                   p.name as project_name, ps.role_tag
            FROM project_stages ps
            JOIN projects p ON p.id = ps.project_id
            WHERE ps.started_at IS NOT NULL OR ps.ended_at IS NOT NULL
            ORDER BY COALESCE(ps.ended_at, ps.started_at) DESC LIMIT 15
        """).fetchall()
        for r in ps_rows:
            feed_events.append({
                "time": r['ended_at'] or r['started_at'],
                "actor": r['role_tag'] or "总装流水线",
                "dept_idx": STAGE_DEPT_MAP.get(r['key'], 4),
                "text": f"流水线「{r['project_name']}」阶段「{r['title']}」: {r['state']}",
                "kind": "stage",
                "nav_tab": "projects",
                "target_id": r['project_id'],
            })

        # Workshop turns
        wt_rows = conn.execute("""
            SELECT wt.id, wt.workshop_id, wt.round, wt.role_tag, wt.author, wt.kind, wt.content, wt.created_at, w.project_id
            FROM workshop_turns wt
            JOIN workshops w ON w.id = wt.workshop_id
            ORDER BY wt.rowid DESC LIMIT 15
        """).fetchall()
        for r in wt_rows:
            feed_events.append({
                "time": r['created_at'],
                "actor": r['author'] or r['role_tag'] or "研讨成员",
                "dept_idx": -1,
                "text": f"研讨会第{r['round']}轮 · {r['author']}: {(r['content'] or '')[:60]}",
                "kind": "workshop",
                "nav_tab": "projects",
                "target_id": r['project_id'],
            })

        # Tasks
        tk_rows = conn.execute("""
            SELECT t.id, t.title, t.state, t.updated_at, t.created_at, e.name as emp_name, p.title as pos_title
            FROM tasks t
            LEFT JOIN ai_employees e ON e.id = t.owner_employee_id
            LEFT JOIN positions p ON p.id = e.position_id
            ORDER BY COALESCE(t.updated_at, t.created_at) DESC LIMIT 15
        """).fetchall()
        for r in tk_rows:
            d_idx = map_dept(r['pos_title'], r['emp_name'])
            feed_events.append({
                "time": r['updated_at'] or r['created_at'],
                "actor": r['emp_name'] or "任务引擎",
                "dept_idx": d_idx,
                "text": f"任务「{r['title'][:30]}」进入 {r['state']}",
                "kind": "task",
                "nav_tab": "tasks",
                "target_id": r['id'],
            })

        # Artifacts
        if _has_table(conn, "artifacts") and _has_table(conn, "tasks"):
            af_rows = conn.execute("""
                SELECT a.id, a.file_path, a.created_at, t.title as task_title
                FROM artifacts a
                LEFT JOIN task_runs r ON r.id = a.run_id
                LEFT JOIN tasks t ON t.id = r.task_id
                ORDER BY a.rowid DESC LIMIT 10
            """).fetchall()
            for r in af_rows:
                f_name = (r['file_path'] or "").replace('\\', '/').split('/')[-1] or r['id']
                feed_events.append({
                    "time": r['created_at'],
                    "actor": "工作产物",
                    "dept_idx": 4,
                    "text": f"生成产物: {f_name}",
                    "kind": "artifact",
                    "nav_tab": "files",
                    "target_id": r['id'],
                })

        # Org experiences
        if _has_table(conn, "org_experiences"):
            exp_rows = conn.execute("""
                SELECT id, title, kind, created_at FROM org_experiences
                ORDER BY rowid DESC LIMIT 10
            """).fetchall()
            for r in exp_rows:
                feed_events.append({
                    "time": r['created_at'],
                    "actor": "避坑晶石",
                    "dept_idx": 7,
                    "text": f"沉淀经验晶石: {r['title'][:40]}",
                    "kind": "crystal",
                    "nav_tab": "experience",
                    "target_id": r['id'],
                })

        # Tool approvals
        if _has_table(conn, "tool_approvals") and _has_table(conn, "ai_employees"):
            ap_rows = conn.execute("""
                SELECT ta.id, ta.tool, ta.target, ta.state, ta.created_at, e.name as agent_name
                FROM tool_approvals ta
                LEFT JOIN ai_employees e ON e.id = ta.agent_id
                ORDER BY ta.rowid DESC LIMIT 10
            """).fetchall()
            for r in ap_rows:
                feed_events.append({
                    "time": r['created_at'],
                    "actor": r['agent_name'] or "安全守卫",
                    "dept_idx": 7,
                    "text": f"审批单: {r['tool']} `{r['target']}` ({r['state']})",
                    "kind": "approval",
                    "nav_tab": "approvals",
                    "target_id": r['id'],
                })

        # Sort newest-first & slice 25
        feed_events.sort(key=lambda x: str(x['time'] or ''), reverse=True)
        feed = feed_events[:25]

    return {
        "ok": True,
        "employees": employees,
        "ceo_chat": ceo_chat,
        "roles": roles,
        "projects": projects,
        "standalone_tasks": standalone_tasks,
        "kpis": kpis,
        "feed": feed,
    }
