"""L0 rule checks: keyword coverage and advertising-law banned phrases.

Both checks are deliberately combination-based. Single-character rules such as
"contains 最" produce false positives on 最近 / 最终 / 第一步, which would block
every normal draft.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import PurePath

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


class _PageCopy(HTMLParser):
    """Collect page copy without interpreting CSS, JavaScript or attributes."""

    _NON_COPY = {"script", "style", "template", "noscript"}
    _BLOCKS = {"address", "article", "aside", "blockquote", "br", "dd", "div",
               "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
               "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
               "main", "nav", "ol", "p", "pre", "section", "table", "td",
               "th", "tr", "ul"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipped: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._NON_COPY:
            self.skipped.append(tag)
        elif not self.skipped and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skipped:
            # Closing an outer ignored element also closes any malformed nested
            # elements, so ordinary copy after it is still checked.
            while self.skipped.pop() != tag:
                pass
        elif not self.skipped and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.skipped and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def text_for_rule(check_ref: str | None, text: str, file_path: str | None = None) -> str:
    """Only HTML advertising checks inspect copy; structural rules keep source.

    The filename is required: prose and Markdown must not be able to hide a
    banned claim inside HTML-looking text or a code example.
    """
    text = text or ""
    if ((check_ref or "").strip() != "banned" or not file_path
            or PurePath(file_path).suffix.lower() not in {".html", ".htm"}):
        return text
    parser = _PageCopy()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


def check_banned(text: str, *, file_path: str | None = None) -> dict:
    text = text_for_rule("banned", text, file_path)
    hits = [p for p in BANNED_PHRASES if p in text]
    return {"ok": not hits, "hits": hits}


def evaluate(check_ref: str | None, text: str, *, file_path: str | None = None) -> dict:
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
        r = check_banned(text, file_path=file_path)
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
