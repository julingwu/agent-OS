"""L0 rule checks: keyword coverage and advertising-law banned phrases.

Both checks are deliberately combination-based. Single-character rules such as
"contains 最" produce false positives on 最近 / 最终 / 第一步, which would block
every normal draft.
"""
from __future__ import annotations

KEYWORD_GROUPS: dict[str, list[str]] = {
    "保湿": ["保湿", "补水", "水润"],
    "屏障": ["屏障", "修护", "修复"],
    "抗老": ["抗老", "抗衰", "抗氧化"],
    "敏感肌": ["敏感肌", "耐受", "舒缓"],
    "换季": ["秋燥", "换季", "秋季", "秋风"],
    "锁水": ["锁水", "滋润", "润泽", "干燥"],
}

# Combinations only. Bare 最 / 第一 are never matched.
BANNED_PHRASES: list[str] = [
    "最好", "最佳", "最强", "最高级", "最低价", "最先进", "最有效",
    "第一品牌", "销量第一", "全国第一", "排名第一",
    "绝对", "100%", "百分之百", "永久", "根治", "治愈", "药到病除", "无副作用",
]


def check_keywords(text: str, min_groups: int = 3,
                   groups: list[list[str]] | None = None) -> dict:
    text = text or ""
    if groups:
        table = {f"组{i+1}": words for i, words in enumerate(groups)}
    else:
        table = KEYWORD_GROUPS
    hit = [g for g, words in table.items() if any(w in text for w in words)]
    return {
        "ok": len(hit) >= min_groups,
        "hit_groups": hit,
        "missing": [g for g in table if g not in hit],
        "required": min_groups,
    }


def check_banned(text: str) -> dict:
    text = text or ""
    hits = [p for p in BANNED_PHRASES if p in text]
    return {"ok": not hits, "hits": hits}


def evaluate(check_ref: str | None, text: str) -> dict:
    """check_ref: 'keywords:<n>' | 'banned' | 'contains:<needle>'."""
    ref = (check_ref or "").strip()
    if ref.startswith("keywords_json:"):
        import json as _json
        payload = _json.loads(ref.split(":", 1)[1])
        n = int(payload.get("n", 3))
        return check_keywords(text, n, groups=payload.get("groups"))
    if ref.startswith("keywords:"):
        n = int(ref.split(":", 1)[1] or 3)
        r = check_keywords(text, n)
        r["detail"] = f"命中 {len(r['hit_groups'])}/{n} 个关键词组"
        return r
    if ref == "banned":
        r = check_banned(text)
        r["detail"] = "无禁用词" if r["ok"] else "命中禁用词: " + "、".join(r["hits"])
        return r
    if ref.startswith("contains_any:"):
        needles = [n for n in ref.split(":", 1)[1].split("|") if n]
        hits = [n for n in needles if n in (text or "")]
        return {"ok": bool(hits),
                "detail": f"命中 {hits[0]}" if hits else f"未包含 {needles}"}
    if ref.startswith("contains:"):
        needle = ref.split(":", 1)[1]
        hit = bool(needle) and needle in (text or "")
        return {"ok": hit, "detail": f"{'包含' if hit else '未包含'} `{needle}`"}
    return {"ok": False, "detail": f"未知规则 {check_ref}"}
