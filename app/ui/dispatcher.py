"""CEO 调度台：老板的模糊需求 → 标签 → 角色库挑人 → 建群 → 派活。

设计取舍（M15 基础版）：
  * 路由先走**确定性标签匹配**（`roles.TAG_WORDS`），结果可解释、可测试；
    标签判不出来时才让模型兜底（下一步），否则每条闲聊都要烧 token。
  * 建群只拉「标签命中」的人：测试用例要求「开发一个计算器」只能拉开发，
    所以这里没有默认 PM，也不加无关角色。
  * 群里第一条派活消息由 CEO 自己发（`author=CEO 调度台`），随后 PM 接手。
"""
from __future__ import annotations

import json
import re
import secrets

from . import roles, store

CEO_TITLE = "CEO 调度台"
CEO_ID = "ID-CEO-DISPATCHER"

# 明确「不是派活」的信号：这些发给 CEO 只做对话，不建群
CHITCHAT = ("你好", "在吗", "谢谢", "你是谁", "帮助", "怎么用", "hello", "hi")


def is_chitchat(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return True
    return len(t) <= 12 and any(w in t for w in CHITCHAT)


def ensure_ceo_chat(conn) -> dict:
    row = conn.execute("SELECT * FROM chats WHERE kind='ceo' LIMIT 1").fetchone()
    if row:
        return dict(row)
    cid = "C-CEO"
    conn.execute("""INSERT OR REPLACE INTO chats (id, title, kind, pinned)
                    VALUES (?,?, 'ceo', 1)""", (cid, CEO_TITLE))
    conn.commit()
    store.add_message(conn, cid, "agent",
                      "我是 CEO 调度台。把需求直接丢给我，我会挑合适的人建群、派活。",
                      author=CEO_TITLE)
    return dict(conn.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone())


def _group_title(text: str, tags: list[str]) -> str:
    head = re.split(r"[，,。；;\n]", (text or "").strip())[0]
    head = head[:16] or "新任务"
    label = {"dev": "开发", "design": "设计", "content": "内容", "data": "数据",
             "qa": "测试", "translate": "翻译"}.get(tags[0] if tags else "", "攻坚")
    return f"{head}·{label}组"


def route(conn, text: str, *, entity_id: str = "LE-GROUP-HQ",
          tenant_id: str = "TENANT-HQ", use_llm_docs: int = 0) -> dict:
    """把一条需求变成**项目**（v1.5）：立项 → 研讨 → 拆解 → … → 复盘。

    标签只用于记录与后续选人；阶段推进由 pipeline 的模板产物与闸门驱动。
    """
    from .. import pipeline

    tags = roles.tags_of(text)
    how, reason = "keyword", ""
    if not tags:
        from . import router_llm
        guess = router_llm.classify(text)
        if guess and guess.get("tags"):
            tags, reason, how = guess["tags"], guess.get("reason") or "", "llm"

    title = (text.strip().splitlines()[0][:24] if text.strip() else "新项目") or "新项目"
    proj = pipeline.create_project(conn, name=title, requirement=text,
                                   entity=entity_id, owner=CEO_ID,
                                   use_llm_docs=use_llm_docs, tenant_id=tenant_id)
    pid = proj["project_id"]

    store.add_message(conn, "C-CEO", "agent",
                      f"已立项 {pid}，按标签【{'/'.join(tags) or '未识别'}】进入 0-1（接需求与立项）。"
                      f"（{how}{'：' + reason if reason else ''}）",
                      author=CEO_TITLE, kind="project",
                      meta=json.dumps({"project_id": pid,
                                       "use_llm_docs": bool(use_llm_docs)},
                                      ensure_ascii=False))

    stages = [k for k, _ in pipeline.STAGES]

    def _run() -> None:
        import threading as _th
        from ..db import get_conn as _gc
        with _gc() as c2:
            try:
                pipeline.run_project(c2, pid, text)
                rows = c2.execute("SELECT state FROM project_stages WHERE project_id=?",
                                  (pid,)).fetchall()
                done = sum(1 for r in rows if r["state"] == "done")
                store.add_message(c2, "C-CEO", "agent",
                                  f"项目 {pid} 跑完 {done}/{len(rows)} 个阶段；"
                                  "立项书/纪要/WBS/发布清单/复盘都在项目目录。",
                                  author=CEO_TITLE)
            except Exception as exc:       # noqa: BLE001
                from ..errors import human_error
                store.add_message(c2, "C-CEO", "agent",
                                  f"项目 {pid} 已暂停：{human_error(exc)}",
                                  author=CEO_TITLE)
            c2.commit()

    import os as _os
    import threading
    if _os.environ.get("M0_DISPATCH_SYNC") == "1":     # 测试里同步跑，避免竞态
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()

    return {"project_id": pid, "tags": tags, "route": how, "reason": reason,
            "stages": stages, "chat_id": "C-CEO", "async": True,
            "members": [], "member_names": []}
