"""CEO 调度台：老板的模糊需求 → 标签 → 流程规划 → 挑人建群 → 派活。

设计取舍：

* 路由先走**确定性标签匹配**（`roles.TAG_WORDS`），结果可解释、可测试；
  标签判不出来时才让模型兜底，否则每条闲聊都要烧 token。
* v4.1 起多了一步**流程规划**（`flows.plan`）：同样先关键词、后模型。规划结果决定
  项目阶段（DAG）与要拉谁进群——分析类需求不会再冒出开发/测试/发布节点。
* 建群只拉**规划里用到的角色**：测试用例要求「开发一个计算器」只能拉开发，
  所以这里没有默认 PM，也不加无关角色。
* 群里第一条派活消息是「作战计划」（阶段 / 角色 / 依赖 / 闸门），由 CEO 发出，
  随后 PM 接手；PM 的汇总读黑板（`project_artifacts`），不重读文件。
"""
from __future__ import annotations

import json
import os
import re
import threading

from . import roles, store

CEO_TITLE = "CEO 调度台"

# 明确「不是派活」的信号：这些发给 CEO 只做对话，不建群
CHITCHAT = ("你好", "在吗", "谢谢", "你是谁", "帮助", "怎么用", "hello", "hi")


def is_chitchat(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return len(t) <= 12 and any(w in t for w in CHITCHAT)


def ensure_ceo_chat(conn, tenant_id=None, entity_id=None) -> dict:
    from ..tenancy import DEFAULT_TENANT, checked_entity_tenant
    tenant_id = (checked_entity_tenant(conn, entity_id, tenant_id)
                 if entity_id else tenant_id or DEFAULT_TENANT)
    row = conn.execute("SELECT * FROM chats WHERE kind='ceo'"
                       " AND COALESCE(tenant_id,'TENANT-HQ')=? LIMIT 1",
                       (tenant_id,)).fetchone()
    if row:
        return dict(row)
    import hashlib
    cid = ("C-CEO" if tenant_id == DEFAULT_TENANT else
           "C-CEO-" + hashlib.sha256(tenant_id.encode()).hexdigest()[:16])
    conn.execute("""INSERT INTO chats (id, title, kind, pinned, tenant_id, entity_id)
                    VALUES (?,?, 'ceo', 1,?,?)""",
                 (cid, CEO_TITLE, tenant_id, entity_id or
                  ("LE-GROUP-HQ" if tenant_id == DEFAULT_TENANT else None)))
    conn.commit()
    store.add_message(conn, cid, "agent",
                      "我是 CEO 调度台。把需求直接丢给我，我会挑合适的人建群、派活。",
                      author=CEO_TITLE)
    return dict(conn.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone())


def _group_title(text: str, tags: list[str]) -> str:
    head = re.split(r"[，,。；;\n]", (text or "").strip())[0]
    head = head[:16] or "新任务"
    label = {"dev": "开发", "design": "设计", "content": "内容", "data": "数据",
             "qa": "测试", "translate": "翻译", "research": "研究", "audit": "审核",
             "product": "产品", "architect": "架构", "ops": "运维"}.get(
                 tags[0] if tags else "", "攻坚")
    return f"{head}·{label}组"


def open_group(conn, text: str, plan: dict, *, entity_id: str = "LE-GROUP-HQ",
               tenant_id: str | None = None) -> dict | None:
    """按规划里的角色建群：同角色多人会全部拉进来（支持并行）；没角色就不建群。"""
    wanted = plan.get("roles") or []
    if not wanted:
        return None
    people = roles.pick(conn, wanted, entity_id=entity_id)
    seen, members = set(), []
    for person in people:
        if person["id"] in seen:
            continue
        seen.add(person["id"])
        members.append({"id": person["id"], "name": person["name"], "tag": person.get("tag")})
    if not members:
        return None
    chat = store.create_group(conn, _group_title(text, wanted),
                              [m["id"] for m in members], tenant_id=tenant_id)
    return {"chat_id": chat["id"], "members": members}


def plan_message(pid: str, plan: dict, group: dict | None,
                 budget: dict | None = None) -> str:
    lines = [f"## 作战计划 · {pid}",
             f"流程：**{plan['name']}**（{len(plan['nodes'])} 步）｜规划依据："
             f"{plan['how']}{'：' + plan['reason'] if plan['reason'] else ''}", "",
             "| # | 阶段 | 负责角色 | 依赖 | 闸门 |", "|---|---|---|---|---|"]
    for i, node in enumerate(plan["nodes"], start=1):
        prev = plan["nodes"][i - 2]["title"] if i > 1 else "—"
        who = "平台" if node["role"] == "platform" else node["role"]
        lines.append(f"| {i} | {node['title']} | {who} | {prev} | {node['gate']} |")
    if group and group.get("members"):
        lines += ["", "群成员：" + "、".join(
            f"{m['name']}（{m['tag']}）" for m in group["members"])]
    if budget:
        from .. import finance
        lines += [finance.plan_table(budget)]
    lines += ["", "执行按上表依赖推进；每段产出先写黑板，PM 收尾时汇总。"]
    return "\n".join(lines)


def assign(conn, pid: str, stage_key: str, employee_id: str, *,
           actor: str | None = None) -> dict:
    """PM 指定某阶段的执行人（同角色多人时用来并行分工）。"""
    from ..audit import write_audit
    stage = conn.execute("SELECT * FROM project_stages WHERE project_id=? AND key=?",
                         (pid, stage_key)).fetchone()
    if stage is None:
        raise KeyError("StageNotFound")
    emp = conn.execute("""SELECT e.id,e.name,e.legal_entity_id,r.tag FROM ai_employees e
                          JOIN role_templates r ON r.id=e.role_id WHERE e.id=?""",
                       (employee_id,)).fetchone()
    if emp is None:
        raise KeyError("EmployeeNotFound")
    proj = conn.execute("SELECT legal_entity_id FROM projects WHERE id=?",
                        (pid,)).fetchone()
    if proj is not None and emp["legal_entity_id"] != proj["legal_entity_id"]:
        raise PermissionError("EmployeeScopeMismatch")
    conn.execute("UPDATE project_stages SET assignee_id=?, role_tag=COALESCE(role_tag,?)"
                 " WHERE project_id=? AND key=?",
                 (emp["id"], emp["tag"], pid, stage_key))
    conn.commit()
    write_audit(conn, legal_entity_id=proj["legal_entity_id"] if proj else "LE-GROUP-HQ",
                verb="project", actor=actor, layer="system", action="stage_assigned",
                target=pid, payload={"stage": stage_key, "employee": emp["id"],
                                     "tag": emp["tag"]}, response={})
    conn.commit()
    return {"project_id": pid, "stage": stage_key, "assignee": emp["id"], "name": emp["name"]}


def route(conn, text: str, *, entity_id: str = "LE-GROUP-HQ",
          use_llm_docs: int = 0, tenant_id: str | None = None,
          actor_id: str | None = None) -> dict:
    """一条需求 → 项目（按规划生成阶段）+ 协作群 + 作战计划 + 后台执行。"""
    from .. import flows, pipeline
    from ..tenancy import checked_entity_tenant
    tenant_id = checked_entity_tenant(conn, entity_id, tenant_id)
    ceo_chat_id = ensure_ceo_chat(conn, tenant_id, entity_id)["id"]

    tags = roles.tags_of(text)
    how, reason = "keyword", ""
    if not tags:
        from . import router_llm
        guess = router_llm.classify(text)
        if guess and guess.get("tags"):
            tags, reason, how = guess["tags"], guess.get("reason") or "", "llm"

    plan = flows.plan(conn, text, tags)            # v4.2：LLM 意图分类 → 流程
    if plan.get("need_clarify"):
        # 置信度 < 0.6：不建项目、不拉人，先问清楚（省一次错派 + 一次烧 token 的执行）
        store.add_message(conn, ceo_chat_id, "agent", plan["question"], author=CEO_TITLE,
                          kind="clarify",
                          meta=json.dumps({"task_type": plan["task_type"],
                                           "confidence": plan["confidence"],
                                           "reason": plan["reason"]}, ensure_ascii=False))
        return {"project_id": None, "clarify": True, "question": plan["question"],
                "tags": tags, "route": plan["how"], "flow": plan,
                "chat_id": ceo_chat_id, "ceo_chat_id": ceo_chat_id,
                "stages": [], "members": [], "member_names": [], "async": False}

    title = (text.strip().splitlines()[0][:24] if text.strip() else "新项目") or "新项目"
    proj = pipeline.create_project(conn, name=title, requirement=text,
                                   entity=entity_id, owner=actor_id,
                                   use_llm_docs=use_llm_docs, tenant_id=tenant_id,
                                   flow=plan)
    pid = proj["project_id"]
    from .. import finance
    budget = finance.open_project(conn, pid, plan["nodes"], requirement=text,
                                  flow_id=plan.get("flow_id") or "", tenant_id=tenant_id)

    group = open_group(conn, text, plan, entity_id=entity_id, tenant_id=tenant_id)
    if group:
        store.add_message(conn, group["chat_id"], "agent",
                          plan_message(pid, plan, group, budget=budget),
                          author=CEO_TITLE, kind="plan",
                          meta=json.dumps({"project_id": pid, "flow_id": plan["flow_id"],
                                           "nodes": [n["key"] for n in plan["nodes"]],
                                           "budget_pool": budget["pool"]},
                                          ensure_ascii=False))

    store.add_message(conn, ceo_chat_id, "agent",
                      f"已立项 {pid}，流程【{plan['name']}】{len(plan['nodes'])} 步"
                      f"（{'/'.join(n['title'] for n in plan['nodes'])}）。"
                      f"预算池 {budget['pool']:,} tokens（弹性 {budget['elastic']:g}）。"
                      f"标签【{'/'.join(tags) or '未识别'}】·{plan['how']}",
                      author=CEO_TITLE, kind="project",
                      meta=json.dumps({"project_id": pid, "flow_id": plan["flow_id"],
                                       "use_llm_docs": bool(use_llm_docs),
                                       "budget_pool": budget["pool"],
                                       "group_id": group["chat_id"] if group else None},
                                      ensure_ascii=False))

    def _run() -> None:
        from ..db import get_conn as _gc
        from ..errors import human_error
        targets = [c for c in (group["chat_id"] if group else None, ceo_chat_id) if c]
        with _gc() as c2:
            try:
                pipeline.run_project(c2, pid, text)
                rows = c2.execute("SELECT state FROM project_stages WHERE project_id=?",
                                  (pid,)).fetchall()
                done = sum(1 for r in rows if r["state"] == "done")
                summary = flows.pm_summary(c2, pid, llm_docs=bool(use_llm_docs))
                body = (f"项目 {pid} 跑完 {done}/{len(rows)} 个阶段。\n\n"
                        + (summary["summary"] or "（黑板为空）"))
                for chat in targets:
                    store.add_message(c2, chat, "agent", body, author=CEO_TITLE,
                                      kind="summary",
                                      meta=json.dumps({"project_id": pid},
                                                      ensure_ascii=False))
            except Exception as exc:       # noqa: BLE001
                for chat in targets:
                    store.add_message(c2, chat, "agent",
                                      f"项目 {pid} 已暂停：{human_error(exc)}",
                                      author=CEO_TITLE)
            c2.commit()

    if os.environ.get("M0_DISPATCH_SYNC") == "1":     # 测试里同步跑，避免竞态
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()

    return {"project_id": pid, "tags": tags, "route": how, "reason": reason,
            "stages": [n["key"] for n in plan["nodes"]], "flow": plan,
            "chat_id": group["chat_id"] if group else ceo_chat_id,
            "ceo_chat_id": ceo_chat_id,
            "members": group["members"] if group else [],
            "member_names": [m["name"] for m in group["members"]] if group else [],
            "async": True}
