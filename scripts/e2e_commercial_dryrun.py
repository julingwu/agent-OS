import os
import sys
import json
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set environment for local run
os.environ["M0_PIPELINE_EXEC"] = "0"  # deterministic verification of all 9 stages

from app import db, pipeline, flows, identity

def main():
    with db.get_conn() as conn:
        owner_row = conn.execute("SELECT id FROM identities WHERE state='active' ORDER BY rowid LIMIT 1").fetchone()
        if not owner_row:
            ident = identity.issue(conn, name="CEO", role="ceo", entity_id="LE-GROUP-HQ")
            owner_id = ident["id"]
        else:
            owner_id = owner_row["id"]
        
        req = "做一个企业级数字员工协作平台落地页产品，包含 HTML 视觉落地页、API 规范 JSON 契约以及产品上线交付说明文档"
        print("=== 启动端到端商业交付订单全流程实测 ===")
        print(f"发起人 Identity: {owner_id}")
        print(f"订单需求: {req}\n")

        # 1. 创建项目
        proj = pipeline.create_project(
            conn,
            name="企业级数字员工协作平台商业交付",
            requirement=req,
            entity="LE-GROUP-HQ",
            owner=owner_id,
            use_llm_docs=0
        )
        pid = proj["project_id"]
        print(f"[OK] 项目已创建: {pid} (预置 {proj['stages']} 个标准总装阶段)")

        # 2. 执行全流程 9 个阶段
        print("\n--- 开始流水线推进 ---")
        run_res = pipeline.run_project(conn, pid, req)
        print(f"[OK] run_project 返回: {run_res}")

        # 3. 校验流水线 9 阶段状态
        stages = conn.execute(
            "SELECT seq, key, title, state FROM project_stages WHERE project_id=? ORDER BY seq", (pid,)
        ).fetchall()
        print("\n=== 流水线阶段执行结果 ===")
        all_done = True
        for s in stages:
            print(f"  Stage {s['seq']:02d} [{s['key']:10s}] - {s['title']:16s} : {s['state']}")
            if s['state'] != "done":
                all_done = False

        # 4. 校验项目整体状态
        p_row = conn.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()
        print(f"\n项目最终生命周期状态: {p_row['state']}")

        # 5. 校验 WBS 动态拆解结果
        wbs_rows = conn.execute(
            "SELECT code, title, role_tag, pack_id, state, depends_on FROM wbs_items WHERE project_id=? ORDER BY rowid",
            (pid,)
        ).fetchall()
        print(f"\n=== WBS 动态分解工单 ({len(wbs_rows)} 项) ===")
        for w in wbs_rows:
            print(f"  {w['code']:4s} | {w['role_tag']:10s} | {w['pack_id']:12s} | 状态: {w['state']:6s} | 依赖: {w['depends_on']:10s} | {w['title']}")

        # 6. 校验阶段交付物
        delivs = conn.execute(
            "SELECT stage_key, kind, file_path, bytes FROM stage_deliverables WHERE project_id=? ORDER BY stage_key",
            (pid,)
        ).fetchall()
        print(f"\n=== 阶段交付物文件 ({len(delivs)} 个) ===")
        for d in delivs:
            fname = os.path.basename(d['file_path'])
            print(f"  [{d['stage_key']:10s}] {fname:28s} ({d['bytes']} B) -> {d['file_path']}")

        # 7. 校验黑板上下文
        bb_rows = conn.execute(
            "SELECT stage_key, role_tag, title, content FROM project_artifacts WHERE project_id=? ORDER BY rowid",
            (pid,)
        ).fetchall()
        print(f"\n=== 黑板上游上下文与共识 ({len(bb_rows)} 条) ===")
        for b in bb_rows:
            snippet = (b['content'] or '').replace('\n', ' ')[:60]
            print(f"  [{b['stage_key']:10s}] {b['role_tag']:8s} | {b['title']}: {snippet}...")

        # 8. 校验阶段闸门
        gates = conn.execute(
            "SELECT stage_key, name, state, note FROM stage_gates WHERE project_id=? ORDER BY rowid",
            (pid,)
        ).fetchall()
        passed_gates = sum(1 for g in gates if g['state'] == 'passed')
        print(f"\n=== 阶段质量闸门 ({passed_gates}/{len(gates)} 通过) ===")
        for g in gates:
            print(f"  [{g['stage_key']:10s}] {g['state'].upper()} - {g['name']} ({g['note'] or ''})")

        conn.commit()

        if all_done and passed_gates == len(gates):
            print(f"\n>>> [SUCCESS] 商业订单端到端全流程 9 阶段 100% 成功交付闭环！项目编号: {pid} <<<")
            return pid
        else:
            print(f"\n>>> [FAIL] 流水线未达到完整完成状态: {p_row['state']} <<<")
            sys.exit(1)

if __name__ == "__main__":
    main()
