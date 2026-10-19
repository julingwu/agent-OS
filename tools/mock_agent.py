"""Mock agent: the same event shape DSH headless emits, without a model call.

Used by tests (and as the fallback runner when the DSH CLI is unavailable) so
the shell's state machine, idempotency, audit, artifacts and acceptance can be
exercised with zero token cost.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ARTICLE = """# 秋日护肤：和干燥温柔相处

一场秋雨过后，空气里的水分像被悄悄抽走，脸颊开始发紧，鼻翼两侧冒出细小的干纹。换季这几天，护肤的重点要往"稳"上放，而不是急着叠加新产品。

第一步是温和清洁。水温调到接近体温，用氨基酸洁面代替皂基，洗完不紧绷才算合格。早上如果出油不多，用清水冲一遍就够，过度清洁会把本来就不厚的皮脂膜洗掉，风一吹就更干。

第二步是补水。洁面后用一支温和的保湿水拍开，让角质层先喝饱水；如果皮肤正在闹情绪，可以换成一次性棉片湿敷三分钟，比反复揉搓更友好。秋燥带来的紧绷感，多半是缺水，而不是"脏"。

第三步是锁水。水分补进去，还要靠油脂把它留在原地。干皮可以选含神经酰胺的面霜，帮屏障把缝隙填上；混油皮用轻薄的乳液就够，T 区少涂一点，避免闷出闭口。屏障稳了，泛红和刺痛会自己安静下来。

第四步才是抗老。白天做好防晒，秋天的紫外线并不比夏天客气；晚上再用含抗氧化成分的精华，节奏放慢，皮肤反而更容易吸收。敏感肌不要一上来就用高浓度活性成分，先修护两周，再慢慢加进来。

面膜不用天天敷。一周两到三次，每次不超过十五分钟，敷完记得涂面霜把水分锁住，否则越敷越干。嘴唇和手背同样需要照顾，随身一支润唇膏和护手霜，比事后补救省事得多。

室内开暖气之后，加湿器比叠加面霜更有用；睡前在床头放一杯水，第二天起床嗓子不干，脸也不容易起皮。

换季不需要一次换掉整套护肤，把清洁变温和、把保湿做扎实、把防晒坚持住，就已经赢过大多数人了。给皮肤一点时间，它会自己找回节奏。"""


MOCK_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent OS · 设计稿</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>body{background:#0b0d12;color:#e7eaf0;font-family:system-ui,"Microsoft YaHei"}</style>
</head>
<body class="min-h-screen">
<div class="flex min-h-screen">
  <aside class="w-64 border-r border-white/10 p-4">
    <div class="text-lg font-semibold mb-4">Agent OS</div>
    <nav class="space-y-2 text-sm text-white/70">
      <div class="rounded-lg bg-white/10 px-3 py-2">会话</div>
      <div class="rounded-lg px-3 py-2">组织架构</div>
      <div class="rounded-lg px-3 py-2">文件库</div>
    </nav>
  </aside>
  <main class="flex-1 p-6">
    <h1 class="text-2xl font-semibold mb-2">设计稿 · 深色玻璃拟态</h1>
    <p class="text-white/60 mb-4">配色：底色 #0b0d12，卡片 rgba(255,255,255,.06)，强调色 #6b8afd</p>
    <div class="rounded-2xl border border-white/10 bg-white/5 p-4 mb-4">
      <div class="text-sm text-white/60 mb-1">小文</div>
      <div>收到，这是项目制任务，我先让设计出方案。</div>
    </div>
    <div class="rounded-2xl border border-white/10 bg-white/5 p-4">
      <div class="text-sm mb-2">验收卡</div>
      <ul class="text-sm text-white/70 space-y-1">
        <li>✅ 产出文件存在</li><li>✅ 页面结构完整</li><li>⏳ 视觉质量待人工</li>
      </ul>
      <div class="mt-3 flex gap-2">
        <button class="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm">验收通过</button>
        <button class="rounded-lg border border-white/20 px-3 py-1.5 text-sm">驳回</button>
      </div>
    </div>
  </main>
</div>
</body></html>"""


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    prompt = sys.stdin.read()
    ws = pathlib.Path(os.environ.get("MOCK_WORKSPACE") or ".")
    ws.mkdir(parents=True, exist_ok=True)

    marker = os.environ.get("MOCK_MARKER")
    if marker:
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write("spawned\n")

    escape = ""
    if os.environ.get("MOCK_ESCAPE") == "1":
        try:
            pathlib.Path("..", "escape.txt").write_text("nope", encoding="utf-8")
            escape = "ESCAPE_OK"
        except OSError as exc:
            escape = f"ESCAPE_BLOCKED:{type(exc).__name__}"

    emit({"type": "session", "sessionId": f"session-mock-{os.getpid()}", "cwd": str(ws)})
    emit({"type": "status", "phase": "turn_start", "turn": 1})
    emit({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
    emit({"type": "thinking", "text": "按模板写内容并落盘。"})
    reviewing = ("评审" in prompt or "审查" in prompt or "review" in prompt.lower())
    target = ws / ("review.md" if reviewing else "article.md")
    body = ("# 评审报告\n\n结论：通过\n\n- 建议补充异常处理\n- 建议把 URL 提成常量\n"
            if reviewing else ARTICLE)
    emit({"type": "tool_call", "callId": "call_mock_1", "tool": "write",
          "input": {"file_path": str(target), "content": body}})
    target.write_text(body, encoding="utf-8")
    emit({"type": "tool_result", "callId": "call_mock_1", "status": "completed",
          "result": f"<path>{target}</path>"})
    emit({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
          "usage": {"inputTokens": 120, "outputTokens": 520,
                    "cacheReadTokens": 0, "cacheWriteTokens": 0, "totalTokens": 640}})
    emit({"type": "text", "text": "DONE"})
    emit({"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}})
    emit({"type": "final", "text": "DONE " + escape if escape else "DONE"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
