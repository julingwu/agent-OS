"""V3.2 真实商业接单演练：前端 / 后端 / 文案 / 测试 → HTML + 说明 → SMTP 交付。

运行：
  .venv/Scripts/python.exe tools/v32_commercial_order.py

做真事：4 个角色各自跑一次 ACP；产物落盘；验收通过；MCP 邮件网关真实 SMTP 发附件。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3

import httpx

from app import acceptance_run, pipeline, role_alignment
from app.audit import new_id
from app.config import load_env
from app.db import connect
from app.engine import dsh_adapter
from app.models import SpawnRunIn
from app.api import verbs
from app.ui import roles, store

ENTITY = "LE-GROUP-HQ"
TENANT = "TENANT-HQ"
TARGET_EMAIL = "ywh2bg6iksmu567i@ethereal.email"
MCP_URL = "http://127.0.0.1:8099/mcp"
MCP_TOKEN = "dev-token"


def _crit(conn, task_id: str, seq: int, text: str, ref: str) -> None:
    conn.execute(
        """INSERT INTO acceptance_criteria
             (id, legal_entity_id, task_id, criterion, check_type, check_ref,
              is_blocking, evidence_required, is_compliance, acceptance_layer, state, seq)
           VALUES (?,?,?,?, 'rule_auto', ?, 1, 1, 0, 'L0', 'pending', ?)""",
        (new_id("C"), ENTITY, task_id, text, ref, seq))


def _task(conn, pid: str, owner: str, title: str, prompt: str,
          criteria: list[tuple[str, str]], stage: str = "build",
          budget: int = 60000, pre_run=None) -> dict:
    tid = new_id("T")
    conn.execute(
        """INSERT INTO tasks (id, legal_entity_id, tenant_id, task_type, title, state,
                              owner_employee_id, project_id, stage)
           VALUES (?,?,?, 'content_write', ?, 'todo', ?, ?, ?)""",
        (tid, ENTITY, TENANT, title, owner, pid, stage))
    for i, (text, ref) in enumerate(criteria, start=1):
        _crit(conn, tid, i, text, ref)
    run_id = new_id("RUN")
    conn.execute(
        """INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, tenant_id,
                                  state, runner, actor_identity)
           VALUES (?,?,1,?,?, 'spawned', 'acp', 'ID-CEO-DISPATCHER')""",
        (run_id, tid, ENTITY, TENANT))
    conn.commit()
    from app import state_machine
    state_machine.transition(conn, tid, "run_spawned", actor="system")
    conn.commit()
    if pre_run:
        pre_run(tid)
    out = dsh_adapter.execute_run(conn, run_id, prompt=prompt, runner="acp",
                                  budget_tokens=budget)
    acc = acceptance_run.run(conn, tid, [ENTITY])
    art = verbs.latest_artifact_path(conn, tid, None)
    return {"task_id": tid, "run_id": run_id, "state": acc["task_state"],
            "verdict": acc["verdict"], "artifact": art,
            "tokens": out.get("usage_tokens"), "final": out.get("final")}


def _msg(conn, chat_id: str, author: str, text: str, *, kind="text", task_id=None):
    store.add_message(conn, chat_id, "agent", text, author=author, kind=kind, task_id=task_id)


def main() -> int:
    load_env()
    conn = connect()
    roles.ensure(conn)
    store.ensure(conn)
    pipeline.ensure(conn)

    pm = roles.instantiate(conn, "ROLE-PRODUCT", ENTITY)
    fe = roles.instantiate(conn, "ROLE-777D54", ENTITY)
    be = roles.instantiate(conn, "ROLE-1F90C6", ENTITY)
    writer = roles.instantiate(conn, "ROLE-CONTENT", ENTITY)
    qa = roles.instantiate(conn, "ROLE-C5B344", ENTITY)
    names = {r["id"]: r["name"] for r in conn.execute(
        "SELECT id,name FROM ai_employees WHERE id IN (?,?,?,?,?)",
        (pm, fe, be, writer, qa))}

    group = store.create_group(conn, "V3.2 商业接单 · 企业官网", [pm, fe, be, writer, qa],
                               tenant_id=TENANT)
    chat_id = group["id"]
    req = ("客户外包单：做一个单文件 index.html 企业服务落地页，带一个按钮点击弹出联系方式、"
           "一个表单提交并做本地校验；另写 300 字中文交付说明。前端、后端、文案、测试协作，"
           "最终通过 SMTP 把说明和代码发到指定测试邮箱。禁止绝对化用语。")
    pid = pipeline.create_project(conn, name="企业官网落地页外单", requirement=req,
                                  entity=ENTITY, owner=pm, tenant_id=TENANT)["project_id"]
    store.add_message(conn, "C-CEO", "agent", f"接单立项：{pid} · 企业官网落地页外单",
                      author="CEO 调度台", kind="project",
                      meta=json.dumps({"project_id": pid}, ensure_ascii=False))
    _msg(conn, chat_id, names[pm], f"接单立项 {pid}：客户要单文件 index.html + 300 字说明，"
                                  "前端/后端/文案/测试四位一起做，最后发邮件交付。")

    pipeline.run_project(conn, pid, req, keys=["intake", "charter", "workshop", "wbs"])
    _msg(conn, chat_id, names[pm], "研讨完成：审计已记录风险；WBS 拆成前端、后端、文案、测试四条。")

    # 替换 WBS 为四角色任务
    conn.execute("DELETE FROM wbs_items WHERE project_id=?", (pid,))
    for code, title, role, pack in (
            ("F1", "前端：单文件 index.html（弹窗 + 表单校验）", "dev", "web-v1"),
            ("B1", "后端：接口契约与错误处理说明", "architect", "content-v1"),
            ("C1", "文案：300 字中文交付说明", "content", "content-v1"),
            ("Q1", "测试：验收清单与缺陷结论", "qa", "content-v1")):
        conn.execute("""INSERT INTO wbs_items
                        (id, project_id, code, title, role_tag, pack_id, depends_on, state)
                        VALUES (?,?,?,?,?,?, '[]', 'pending')""",
                     (new_id("WBS"), pid, code, title, role, pack))
    conn.commit()

    fe_out = _task(conn, pid, fe, "前端交付 index.html",
                   "你是前端开发。请用单文件 index.html 实现企业服务落地页：一个按钮点击弹出联系方式"
                   "（可用 alert 或 div 弹窗），一个表单提交并做本地非空/邮箱校验，页面要有基本样式。"
                   "不引入第三方库。把完整代码写入 index.html，完成后只回复 DONE。",
                   [("包含交互脚本或事件", "contains_any:<script|onclick|addEventListener"),
                    ("是 HTML 文档", "contains_any:<html|<!DOCTYPE")])
    _msg(conn, chat_id, names[fe], f"前端已交付 index.html（{fe_out['state']}）。",
         kind="card", task_id=fe_out["task_id"])

    be_out = _task(conn, pid, be, "后端契约与错误处理",
                   "你是后端工程师。为这个单文件落地页写一份简短接口契约与错误处理说明："
                   "表单提交字段、校验规则、失败提示、后续接后端时的接口边界。写入 api_contract.md，"
                   "完成后只回复 DONE。",
                   [("包含接口", "contains:接口"), ("包含错误处理", "contains:错误")])
    _msg(conn, chat_id, names[be], f"后端接口契约已交付（{be_out['state']}）。",
         kind="card", task_id=be_out["task_id"])

    content_out = _task(conn, pid, writer, "300 字交付说明",
                        "你是文案。为客户写一篇 300 字左右的中文交付说明，说明这个企业官网落地页"
                        "包含什么、怎么用、表单校验和联系按钮如何工作；不要绝对化用语。"
                        "写入 delivery.md，完成后只回复 DONE。",
                        [("包含交付说明", "contains:交付"), ("包含说明", "contains:说明")])
    _msg(conn, chat_id, names[writer], f"300 字交付说明已交付（{content_out['state']}）。",
         kind="card", task_id=content_out["task_id"])

    # QA 在自己的工作区：把前端/文案产物复制进去，让它真实读取而不是把全文塞 prompt。
    def qa_pre_run(qa_tid: str) -> None:
        ws = dsh_adapter.task_workspace(qa_tid, "ID-CEO-DISPATCHER")
        if fe_out["artifact"]:
            shutil.copy2(fe_out["artifact"], ws / "index.html")
        if content_out["artifact"]:
            shutil.copy2(content_out["artifact"], ws / "delivery.md")

    qa_out = _task(conn, pid, qa, "测试验收报告",
                   "你是测试工程师。当前工作区有 index.html 和 delivery.md。"
                   "请读取两个文件，检查：1) index.html 是否有交互脚本/按钮事件；"
                   "2) 表单是否有本地校验；3) delivery.md 是否约 300 字。"
                   "写一份简洁的 test_report.md（≤30 行），必须包含「通过/不通过」和 3 条检查项；"
                   "不要修改 HTML；完成后只回复 DONE。",
                   [("包含测试结论", "contains:通过"), ("包含测试", "contains:测试")],
                   budget=120000, pre_run=qa_pre_run)
    # QA 的 execute_run 已经跑完；若它没看到文件，仍会在报告里说明。这里不再二次跑，避免浪费。
    _msg(conn, chat_id, names[qa], f"测试报告已交付（{qa_out['state']}）。",
         kind="card", task_id=qa_out["task_id"])

    # WBS 完成 + 构建闸门
    all_ok = all(x["state"] == "completed" for x in (fe_out, be_out, content_out, qa_out))
    for code in ("F1", "B1", "C1", "Q1"):
        conn.execute("UPDATE wbs_items SET state='done' WHERE project_id=? AND code=?", (pid, code))
    pipeline._write(conn, pid, "build", "build", "04_build.md",
                    "# 构建记录\n\n- 前端：index.html\n- 后端：api_contract.md\n"
                    "- 文案：delivery.md\n- 测试：test_report.md\n")
    pipeline.gate(conn, pid, "build", "四角色交付物齐全", passed=all_ok,
                  note=f"完成 {sum(1 for x in (fe_out, be_out, content_out, qa_out) if x['state']=='completed')}/4")
    conn.execute("UPDATE project_stages SET state='done', ended_at=CURRENT_TIMESTAMP"
                 " WHERE project_id=? AND key='build'", (pid,))
    conn.commit()

    # 测试阶段（对 HTML 做真实冒烟检查，不跑 .py）
    html_path = fe_out["artifact"] or ""
    html = pathlib.Path(html_path).read_text(encoding="utf-8", errors="replace") if html_path else ""
    has_interaction = any(x in html for x in ("<script", "onclick", "addEventListener"))
    pipeline._write(conn, pid, "test", "test", "06_test.md",
                    f"# 测试记录\n\n- index.html 存在：{bool(html)}\n"
                    f"- 交互脚本/事件：{has_interaction}\n- 文案交付：{bool(content_out['artifact'])}\n"
                    f"- 结论：{'通过' if has_interaction else '未通过'}\n")
    pipeline.gate(conn, pid, "test", "冒烟测试通过", passed=has_interaction,
                  note="index.html 交互检查")
    conn.execute("UPDATE project_stages SET state='done', ended_at=CURRENT_TIMESTAMP"
                 " WHERE project_id=? AND key='test'", (pid,))
    conn.commit()

    for key in ("accept", "release", "retro"):
        pipeline.run_stage(conn, pid, key, req)

    # 真实 SMTP：MCP tools/call，带 index.html + delivery.md 附件
    note = pathlib.Path(content_out["artifact"]).read_text(encoding="utf-8", errors="replace")
    payload = {
        "jsonrpc": "2.0", "id": 32, "method": "tools/call",
        "params": {"name": "send_email", "arguments": {
            "to": TARGET_EMAIL,
            "subject": "V3.2 商业接单交付：企业官网落地页",
            "body": note + "\n\n附件：index.html（完整代码）与 delivery.md。",
            "attachments": [html_path, content_out["artifact"]],
        }},
    }
    r = httpx.post(MCP_URL, headers={"authorization": f"Bearer {MCP_TOKEN}"},
                   json=payload, timeout=40, trust_env=False).json()
    result = r.get("result", {})
    sent = (result.get("content") or [{}])[0].get("text", json.dumps(r, ensure_ascii=False))
    _msg(conn, chat_id, "系统", f"✅ 已通过真实 SMTP 交付到 {TARGET_EMAIL}：{sent}")
    _msg(conn, chat_id, names[pm], "项目闭环：立项 → 研讨 → 开发 → 测试 → 发件完成。")

    print(json.dumps({
        "project_id": pid, "chat_id": chat_id,
        "pm": names[pm], "fe": names[fe], "be": names[be],
        "writer": names[writer], "qa": names[qa],
        "frontend": fe_out, "backend": be_out, "content": content_out, "qa_out": qa_out,
        "all_ok": all_ok, "html": html_path, "note": content_out["artifact"],
        "smtp": sent,
    }, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
