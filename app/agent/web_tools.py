"""Web search, web extraction, and headless browser tools for AgentOS.

Engineered for zero-key resilient execution in Mainland China and global environments:
- Domestic Multi-Engine Cascade: Bing RSS, 360 Search, Sogou, Baidu News RSS.
- Optional Pro Search API (Tavily, Bocha, Serper) when search_api_key or env vars are set.
- Headless Chromium DOM rendering and full-page screenshot artifact capture.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from .. import db

log = logging.getLogger("app.agent.web_tools")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _find_browser_binary() -> str | None:
    """Locate headless Chromium / Chrome / Edge on Linux or Windows."""
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _get_search_api_key() -> tuple[str | None, str]:
    """Retrieve search API key from app_settings or environment.

    Returns (key, provider_type) where provider_type is 'tavily', 'bocha', 'serper', or 'none'.
    """
    key = os.environ.get("TAVILY_API_KEY")
    if key:
        return key.strip(), "tavily"
    key = os.environ.get("BOCHA_API_KEY")
    if key:
        return key.strip(), "bocha"
    key = os.environ.get("SERPER_API_KEY")
    if key:
        return key.strip(), "serper"

    try:
        with db.get_conn() as conn:
            row = conn.execute("SELECT value_json FROM app_settings WHERE key='search_api_key'").fetchone()
            if row:
                stored = json.loads(row["value_json"])
                if isinstance(stored, str) and stored.strip():
                    k = stored.strip()
                    if k.startswith("tvly-"):
                        return k, "tavily"
                    if k.startswith("sk-"):
                        return k, "bocha"
                    return k, "custom"
    except Exception:
        pass
    return None, "none"


# ---------------------------------------------------------------- Search Engines
def _search_pro_api(query: str, key: str, provider: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            if provider == "tavily" or key.startswith("tvly-"):
                r = client.post(
                    "https://api.tavily.com/search",
                    json={"query": query, "search_depth": "basic", "max_results": max_results},
                )
                if r.status_code == 200:
                    for item in r.json().get("results", []):
                        results.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "snippet": item.get("content", ""),
                            "source": "tavily",
                            "time": item.get("published_date", ""),
                        })
            elif provider == "bocha" or key.startswith("sk-"):
                r = client.post(
                    "https://api.bochaai.com/v1/web-search",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"query": query, "count": max_results},
                )
                if r.status_code == 200:
                    for item in r.json().get("data", {}).get("webPages", {}).get("value", []):
                        results.append({
                            "title": item.get("name", ""),
                            "url": item.get("url", ""),
                            "snippet": item.get("snippet", "") or item.get("summary", ""),
                            "source": item.get("siteName", "bocha"),
                            "time": item.get("dateLastCrawled", ""),
                        })
    except Exception as exc:
        log.warning("Pro search API failed: %s", exc)
    return results


def _search_bing_rss(query: str, search_type: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    endpoints = []
    if search_type in ("news", "all"):
        endpoints.append(f"https://cn.bing.com/news/search?q={urllib.parse.quote(query)}&format=rss")
    if search_type in ("web", "all"):
        endpoints.append(f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&format=rss")

    with httpx.Client(headers=HEADERS, timeout=8.0, follow_redirects=True, trust_env=False) as client:
        for ep in endpoints:
            try:
                r = client.get(ep)
                if r.status_code != 200 or not r.text.strip():
                    continue
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title = item.findtext("title") or ""
                    link = item.findtext("link") or ""
                    desc = item.findtext("description") or ""
                    pub_date = item.findtext("pubDate") or ""
                    # Strip any HTML tags inside description
                    desc_clean = re.sub(r"<[^>]+>", "", desc).strip()
                    if title and link:
                        results.append({
                            "title": title.strip(),
                            "url": link.strip(),
                            "snippet": desc_clean,
                            "source": "bing",
                            "time": pub_date.strip(),
                        })
                    if len(results) >= max_results:
                        break
            except Exception as exc:
                log.debug("Bing RSS fetch failed for %s: %s", ep, exc)
            if len(results) >= max_results:
                break
    return results


def _search_so_360(query: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    url = f"https://www.so.com/s?q={urllib.parse.quote(query)}"
    try:
        with httpx.Client(headers=HEADERS, timeout=8.0, follow_redirects=True, trust_env=False) as client:
            r = client.get(url)
            if r.status_code == 200 and r.text:
                soup = BeautifulSoup(r.text, "html.parser")
                for li in soup.select("ul.result li.res-list")[:max_results]:
                    h3 = li.select_one("h3.res-title a")
                    if not h3:
                        continue
                    title = h3.get_text(strip=True)
                    href = h3.get("href") or ""
                    desc_el = li.select_one(".res-desc, .res-rich, p")
                    snippet = desc_el.get_text(strip=True) if desc_el else ""
                    if title and href:
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet,
                            "source": "360",
                            "time": "",
                        })
    except Exception as exc:
        log.debug("360 search failed: %s", exc)
    return results


def _search_sogou(query: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    url = f"https://www.sogou.com/web?query={urllib.parse.quote(query)}"
    try:
        with httpx.Client(headers=HEADERS, timeout=8.0, follow_redirects=True, trust_env=False) as client:
            r = client.get(url)
            if r.status_code == 200 and r.text:
                soup = BeautifulSoup(r.text, "html.parser")
                for box in soup.select(".vrwrap, .results .rb")[:max_results]:
                    h3 = box.select_one("h3 a")
                    if not h3:
                        continue
                    title = h3.get_text(strip=True)
                    href = h3.get("href") or ""
                    if href.startswith("/link?url="):
                        href = f"https://www.sogou.com{href}"
                    desc_el = box.select_one(".star-wiki, .text-layout, p.str_info, .space-txt")
                    snippet = desc_el.get_text(strip=True) if desc_el else ""
                    if title and href:
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet,
                            "source": "sogou",
                            "time": "",
                        })
    except Exception as exc:
        log.debug("Sogou search failed: %s", exc)
    return results


def _search_chromium_fallback(query: str, max_results: int) -> list[dict]:
    """Headless Chromium DOM dump fallback if HTTP engines are blocked."""
    browser = _find_browser_binary()
    if not browser:
        return []
    results: list[dict] = []
    target_url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&ensearch=0"
    try:
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--dump-dom",
            target_url,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode == 0 and proc.stdout:
            soup = BeautifulSoup(proc.stdout, "html.parser")
            for algo in soup.select("#b_results .b_algo")[:max_results]:
                h2 = algo.select_one("h2 a")
                if not h2:
                    continue
                title = h2.get_text(strip=True)
                href = h2.get("href") or ""
                p = algo.select_one(".b_caption p, .b_snippet")
                snippet = p.get_text(strip=True) if p else ""
                if title and href:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                        "source": "bing-chromium",
                        "time": "",
                    })
    except Exception as exc:
        log.debug("Chromium search fallback failed: %s", exc)
    return results


def web_search(query: str, search_type: str = "all", max_results: int = 8, count: int | None = None) -> dict:
    """全网实时搜索。聚合国内可用引擎与可选 Pro API，自动去重输出结构化结果。"""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "搜索词 query 不能为空"}
    eff_limit = count if count is not None else max_results
    limit = max(1, min(int(eff_limit or 8), 20))

    collected: list[dict] = []

    # 1. Check Pro Search API if configured
    api_key, provider = _get_search_api_key()
    if api_key and provider != "none":
        pro_results = _search_pro_api(q, api_key, provider, limit)
        collected.extend(pro_results)

    # 2. Domestic Multi-Engine Cascade
    if len(collected) < limit:
        # Engine A: Bing RSS (Fastest & most reliable on Tencent Cloud Mainland)
        bing_res = _search_bing_rss(q, search_type, limit - len(collected))
        collected.extend(bing_res)

    if len(collected) < limit:
        # Engine B: 360 Search
        so_res = _search_so_360(q, limit - len(collected))
        collected.extend(so_res)

    if len(collected) < limit:
        # Engine C: Sogou
        sogou_res = _search_sogou(q, limit - len(collected))
        collected.extend(sogou_res)

    if not collected:
        # Engine D: Headless Chromium DOM dump
        collected.extend(_search_chromium_fallback(q, limit))

    # Deduplicate by URL and title
    seen_urls = set()
    deduped: list[dict] = []
    for item in collected:
        u = item.get("url", "").rstrip("/")
        t = item.get("title", "").strip()
        if not u or not t:
            continue
        key = (u, t[:25])
        if key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(item)
        if len(deduped) >= limit:
            break

    return {
        "ok": True,
        "query": q,
        "count": len(deduped),
        "results": deduped,
    }


# ---------------------------------------------------------------- HTML to Markdown
def _html_to_markdown(soup: BeautifulSoup, base_url: str = "") -> str:
    # 1. Remove noise elements
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "aside", "iframe", "form", "button"]):
        tag.decompose()

    # Convert links to markdown
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True)
        href = a["href"].strip()
        if link_text and href and not href.startswith("javascript:"):
            if base_url and not href.startswith(("http://", "https://", "mailto:")):
                href = urllib.parse.urljoin(base_url, href)
            a.replace_with(f"[{link_text}]({href})")

    lines: list[str] = []

    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote"]):
        name = el.name
        if name == "h1":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n# {text}\n")
        elif name == "h2":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n## {text}\n")
        elif name == "h3":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n### {text}\n")
        elif name in ("h4", "h5", "h6"):
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n#### {text}\n")
        elif name == "p":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n{text}\n")
        elif name == "li":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"- {text}")
        elif name == "blockquote":
            text = el.get_text(strip=True)
            if text:
                lines.append(f"\n> {text}\n")
        elif name == "pre":
            code = el.get_text()
            if code.strip():
                lines.append(f"\n```\n{code.strip()}\n```\n")
        elif name == "table":
            rows = el.find_all("tr")
            if rows:
                table_lines = []
                for idx, tr in enumerate(rows[:50]):
                    cells = [c.get_text(strip=True).replace("|", "\\|") for c in tr.find_all(["th", "td"])]
                    if not cells:
                        continue
                    row_str = "| " + " | ".join(cells) + " |"
                    table_lines.append(row_str)
                    if idx == 0:
                        divider = "| " + " | ".join(["---"] * len(cells)) + " |"
                        table_lines.append(divider)
                if table_lines:
                    lines.append("\n" + "\n".join(table_lines) + "\n")

    md = "\n".join(lines)
    # Compress 3+ consecutive newlines
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md


def web_fetch(url: str, max_chars: int = 20000, render_js: bool = False) -> dict:
    """抓取网页正文，剥离广告脚本，脱水转换为结构化 Markdown。支持动态渲染降级。"""
    raw_url = (url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}

    limit = max(100, min(int(max_chars or 20000), 100000))

    if render_js:
        return browser_open(raw_url, wait_ms=2500, screenshot=False, max_chars=limit)

    final_url = raw_url
    title = ""
    markdown_content = ""

    try:
        with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(raw_url)
            final_url = str(resp.url)

            # Check Sogou meta-refresh redirect
            if "sogou.com/link" in final_url and ("window.location.replace" in resp.text or 'http-equiv="refresh"' in resp.text):
                m = re.search(r'replace\("([^"]+)"\)', resp.text) or re.search(r'url=\'?([^\'"]+)', resp.text)
                if m:
                    target_url = m.group(1)
                    if target_url.startswith("/"):
                        target_url = urllib.parse.urljoin(final_url, target_url)
                    resp = client.get(target_url)
                    final_url = str(resp.url)

            content_type = resp.headers.get("content-type", "").lower()
            if "json" in content_type:
                try:
                    data = resp.json()
                    formatted = json.dumps(data, ensure_ascii=False, indent=2)
                    return {"ok": True, "url": final_url, "title": "JSON Response",
                            "content": formatted[:limit], "chars": len(formatted[:limit])}
                except Exception:
                    pass

            # Detect encoding
            encoding = resp.encoding or "utf-8"
            html_text = resp.content.decode(encoding, errors="replace")

            soup = BeautifulSoup(html_text, "html.parser")
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""

            markdown_content = _html_to_markdown(soup, base_url=final_url)

    except Exception as exc:
        log.warning("web_fetch HTTP failed for %s (%s), trying browser fallback", raw_url, exc)
        return browser_open(raw_url, wait_ms=2500, screenshot=False, max_chars=limit)

    # If extracted visible text is too brief (< 120 chars) and page has scripts, likely SPA
    if len(markdown_content) < 120:
        browser = _find_browser_binary()
        if browser:
            log.info("web_fetch content too brief (%d chars), falling back to browser_open", len(markdown_content))
            return browser_open(final_url, wait_ms=2500, screenshot=False, max_chars=limit)

    if len(markdown_content) > limit:
        markdown_content = markdown_content[:limit] + "\n\n...[正文过长，已截断]"

    return {
        "ok": True,
        "url": final_url,
        "title": title or final_url,
        "content": markdown_content,
        "chars": len(markdown_content),
    }


def browser_open(url: str, wait_ms: int = 2500, screenshot: bool = False,
                 selector: str | None = None, max_chars: int = 20000, emit=None) -> dict:
    """使用服务端无头浏览器完整加载并渲染页面。支持动态 JS 页面正文提取与截屏挂载。"""
    raw_url = (url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}

    browser = _find_browser_binary()
    if not browser:
        return {"ok": False, "error": "服务器未检测到 Chromium / Chrome 无头浏览器二进制文件"}

    limit = max(100, min(int(max_chars or 20000), 100000))
    title = ""
    content = ""
    screenshot_art = None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_png = os.path.join(tmpdir, "shot.png")
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--window-size=1280,800",
            f"--virtual-time-budget={max(1000, int(wait_ms or 2500))}",
            "--dump-dom",
        ]
        if screenshot:
            cmd.append(f"--screenshot={tmp_png}")
        cmd.append(raw_url)

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if proc.returncode != 0 and not proc.stdout:
                return {"ok": False, "error": f"浏览器渲染失败 (exit {proc.returncode}): {proc.stderr[:300]}"}

            dom_text = proc.stdout or ""
            soup = BeautifulSoup(dom_text, "html.parser")
            t_tag = soup.find("title")
            title = t_tag.get_text(strip=True) if t_tag else raw_url

            content = _html_to_markdown(soup, base_url=raw_url)
            if len(content) > limit:
                content = content[:limit] + "\n\n...[正文过长，已截断]"

            if screenshot and os.path.exists(tmp_png) and os.path.getsize(tmp_png) > 0:
                from .. import artifacts
                png_bytes = open(tmp_png, "rb").read()
                art = artifacts.save_bytes(
                    f"web_{int(time.time())}.png",
                    png_bytes,
                    kind="image/png",
                )
                screenshot_art = {
                    "id": art["id"],
                    "url": art["url"],
                    "name": art["name"],
                    "bytes": art["bytes"],
                }
                if emit:
                    try:
                        emit("artifact.attached", {
                            "artifact_id": art["id"],
                            "name": art["name"],
                            "kind": art["kind"],
                            "bytes": art["bytes"],
                            "url": art["url"],
                        })
                    except Exception:
                        pass

        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"网页加载渲染超时 ({wait_ms}ms)"}
        except Exception as exc:
            return {"ok": False, "error": f"无头浏览器执行异常: {exc}"}

    return {
        "ok": True,
        "url": raw_url,
        "title": title,
        "content": content,
        "chars": len(content),
        "screenshot": screenshot_art,
    }
