import os
import sys
import uuid
import time
import pathlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 明确加载 .env 配置
from dotenv import load_dotenv
load_dotenv()
os.environ["M0_SANDBOX_ACP"] = "0"

from app import db, identity, state_machine
from app.engine import dsh_adapter, task_generator
from app import acceptance_run

def live_run():
    print("=== 开始 DeepSeek ACP 真实在线大模型演练 ===")
    
    with db.get_conn() as conn:
        owner_row = conn.execute("SELECT id FROM identities WHERE state='active' ORDER BY rowid LIMIT 1").fetchone()
        owner_id = owner_row["id"] if owner_row else "ID-7f3ed41c41ae45f9"
        
        brief = "写一个 Python 命令行计算器脚本 calc.py，支持加减乘除四则运算，并编写简要使用文档 README.md"
        print(f"发起人: {owner_id}")
        print(f"任务目标: {brief}\n")
        
        # 1. 生成任务
        gen = task_generator.generate(conn, brief, legal_entity_id="LE-GROUP-HQ", pack_id="code-local-v1")
        tid = gen["task_id"]
        print(f"[OK] 任务已生成: {tid}")
        
        # 2. 状态机流转为 assigned
        run_id = "RUN-LIVE-" + uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO task_runs (id, task_id, run_seq, legal_entity_id, state, runner, actor_identity)"
            " VALUES (?, ?, 1, 'LE-GROUP-HQ', 'spawned', 'acp', ?)",
            (run_id, tid, owner_id)
        )
        try:
            state_machine.transition(conn, tid, "run_spawned", actor="system")
        except Exception:
            pass
        conn.commit()
        
        print(f"正在调起 DSH ACP 引擎并请求 DeepSeek 官方模型 (Run ID: {run_id})...")
        t0 = time.time()
        
        # 3. 调用真实 ACP 执行
        out = dsh_adapter.execute_run(
            conn,
            run_id=run_id,
            runner="acp",
            budget_tokens=40000,
            pause_at_tokens=35000
        )
        elapsed = time.time() - t0
        print(f"\n[OK] 模型执行结束，耗时: {elapsed:.2f} 秒")
        print("执行结果概要:")
        print(f"  - 最终状态: {out.get('state')}")
        print(f"  - 任务状态: {out.get('task_state')}")
        print(f"  - 消耗 Tokens: {out.get('usage_tokens')}")
        print(f"  - 停止原因: {out.get('reason')}")
        
        # 4. 检查工作区交付物
        ws = dsh_adapter.task_workspace(tid, owner_id)
        print(f"\n工作区目录: {ws}")
        files = [f for f in ws.iterdir() if f.is_file() and not f.name.startswith(".")]
        print(f"产出文件数量: {len(files)}")
        for f in files:
            print(f"  - {f.name} ({f.stat().st_size} 字节)")
            if f.suffix in (".py", ".md", ".txt"):
                content_preview = f.read_text(encoding="utf-8", errors="ignore")[:300]
                print(f"    [预览]:\n{content_preview}...\n")
        
        # 5. 执行真实验收
        reviewed = acceptance_run.run(conn, tid, ["LE-GROUP-HQ"])
        print(f"验收裁决: {reviewed.get('task_state')}")
        print(f"判据详情: {reviewed.get('verdicts')}")
        conn.commit()

if __name__ == "__main__":
    live_run()
