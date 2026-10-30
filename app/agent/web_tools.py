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
            elif provider == "serper" or key.startswith("serper_") or (len(key) == 40 and not key.startswith("sk-")):
                headers = {"X-API-KEY": key, "Content-Type": "application/json"}
                r = client.post("https://google.serper.dev/search",
                                json={"q": query, "num": max_results, "gl": "cn", "hl": "zh-cn"},
                                headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    for item in d.get("news", []) + d.get("organic", []):
                        results.append({
                            "title": item.get("title", ""),
                            "url": item.get("link", ""),
                            "snippet": item.get("snippet", ""),
                            "source": "serper",
                            "time": item.get("date", ""),
                        })
    except Exception as exc:
        log.warning("Pro search API failed: %s", exc)
    return results


def _search_bing_rss(query: str, search_type: str, max_results: int) -> list[dict]:
    results: list[dict] = []
    endpoints = []
    if search_type in ("news", "all"):
        endpoints.append(f"https://cn.bing.com/news/search?q={urllib.parse.quote_plus(query)}&format=rss")
    if search_type in ("web", "all"):
        endpoints.append(f"https://cn.bing.com/search?q={urllib.parse.quote_plus(query)}&format=rss")

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


def _search_baidu_news(query: str, max_results: int) -> list[dict]:
    """百度新闻源检索：针对国内科技发布会、企业快讯与最新动态。"""
    results: list[dict] = []
    url = f"https://www.baidu.com/s?rtt=1&bsst=1&cl=2&tn=news&word={urllib.parse.quote_plus(query)}"
    try:
        with httpx.Client(headers=HEADERS, timeout=8.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.text:
                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.find_all(class_=re.compile(r"result-op|c-container")):
                    t_tag = item.find("h3") or item.find(class_=re.compile(r"news-title|c-title"))
                    a_tag = t_tag.find("a") if t_tag else None
                    if not a_tag:
                        continue
                    title = a_tag.get_text(strip=True)
                    link = a_tag.get("href", "")
                    summary_tag = item.find(class_=re.compile(r"c-font-normal|c-color-text2|news-content"))
                    snippet = summary_tag.get_text(strip=True) if summary_tag else ""
                    src_tag = item.find(class_=re.compile(r"c-color-gray|c-color-gray2|source"))
                    time_info = src_tag.get_text(strip=True) if src_tag else ""
                    if title and link:
                        results.append({
                            "title": title,
                            "url": link,
                            "snippet": snippet,
                            "source": "baidu-news",
                            "time": time_info,
                        })
                    if len(results) >= max_results:
                        break
    except Exception as exc:
        log.debug("Baidu news search failed: %s", exc)
    return results


def _search_sogou_weixin(query: str, max_results: int) -> list[dict]:
    """搜狗微信公众号深度文章检索。"""
    results: list[dict] = []
    url = f"https://weixin.sogou.com/weixin?type=2&query={urllib.parse.quote_plus(query)}"
    try:
        with httpx.Client(headers=HEADERS, timeout=8.0, follow_redirects=True, trust_env=False) as client:
            resp = client.get(url)
            if resp.status_code == 200 and resp.text:
                soup = BeautifulSoup(resp.text, "html.parser")
                for li in soup.find_all("li", id=re.compile(r"sogou_vr_")):
                    h3 = li.find("h3")
                    a = h3.find("a") if h3 else None
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    link = a.get("href", "")
                    if link.startswith("/"):
                        link = urllib.parse.urljoin("https://weixin.sogou.com", link)
                    txt_info = li.find(class_="txt-info")
                    snippet = txt_info.get_text(strip=True) if txt_info else ""
                    source_tag = li.find(class_="account") or li.find(class_="s-p")
                    source = source_tag.get_text(strip=True) if source_tag else "微信公众号"
                    if title and link:
                        results.append({
                            "title": title,
                            "url": link,
                            "snippet": snippet,
                            "source": f"sogou-wx({source})",
                            "time": "",
                        })
                    if len(results) >= max_results:
                        break
    except Exception as exc:
        log.debug("Sogou WeChat search failed: %s", exc)
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
            "--headless=new",
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
    # Engine A: Baidu News (priority for news/press releases)
    if search_type in ("news", "all") and len(collected) < limit:
        baidu_res = _search_baidu_news(q, limit - len(collected))
        collected.extend(baidu_res)

    # Engine B: Sogou WeChat Official Accounts
    if len(collected) < limit:
        wx_res = _search_sogou_weixin(q, limit - len(collected))
        collected.extend(wx_res)

    # Engine C: Bing RSS (Fastest on Tencent Cloud)
    if len(collected) < limit:
        bing_res = _search_bing_rss(q, search_type, limit - len(collected))
        collected.extend(bing_res)

    # Engine D: 360 Search
    if len(collected) < limit:
        so_res = _search_so_360(q, limit - len(collected))
        collected.extend(so_res)

    # Engine E: Sogou Web
    if len(collected) < limit:
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

    MAX_CONTENT_LENGTH = 15 * 1024 * 1024  # 15MB

    try:
        with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", raw_url) as resp:
                if resp.status_code >= 400:
                    return {"ok": False, "error": f"网页请求失败 HTTP {resp.status_code}"}

                cl_header = resp.headers.get("content-length")
                if cl_header:
                    try:
                        if int(cl_header) > MAX_CONTENT_LENGTH:
                            return {"ok": False, "error": f"网页内容过大 ({cl_header} 字节)，超过 15MB 限制"}
                    except (ValueError, TypeError):
                        pass

                chunks = []
                total_bytes = 0
                for chunk in resp.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > MAX_CONTENT_LENGTH:
                        return {"ok": False, "error": "网页内容超过 15MB 限制，已终止下载"}
                    chunks.append(chunk)

                content_bytes = b"".join(chunks)
                final_url = str(resp.url)
                content_type = resp.headers.get("content-type", "").lower()
                encoding = resp.encoding or "utf-8"

            # Check Sogou meta-refresh redirect
            text_preview = content_bytes[:4096].decode("utf-8", errors="ignore")
            if "sogou.com/link" in final_url and ("window.location.replace" in text_preview or 'http-equiv="refresh"' in text_preview):
                m = re.search(r'replace\("([^"]+)"\)', text_preview) or re.search(r'url=\'?([^\'"]+)', text_preview)
                if m:
                    target_url = m.group(1)
                    if target_url.startswith("/"):
                        target_url = urllib.parse.urljoin(final_url, target_url)
                    with client.stream("GET", target_url) as resp2:
                        if resp2.status_code >= 400:
                            return {"ok": False, "error": f"网页请求失败 HTTP {resp2.status_code}"}
                        cl2 = resp2.headers.get("content-length")
                        if cl2:
                            try:
                                if int(cl2) > MAX_CONTENT_LENGTH:
                                    return {"ok": False, "error": f"网页内容过大 ({cl2} 字节)，超过 15MB 限制"}
                            except (ValueError, TypeError):
                                pass
                        chunks2 = []
                        tb2 = 0
                        for chunk in resp2.iter_bytes():
                            tb2 += len(chunk)
                            if tb2 > MAX_CONTENT_LENGTH:
                                return {"ok": False, "error": "网页内容超过 15MB 限制，已终止下载"}
                            chunks2.append(chunk)
                        content_bytes = b"".join(chunks2)
                        final_url = str(resp2.url)
                        content_type = resp2.headers.get("content-type", "").lower()
                        encoding = resp2.encoding or "utf-8"

            if "json" in content_type:
                try:
                    data = json.loads(content_bytes.decode(encoding, errors="replace"))
                    formatted = json.dumps(data, ensure_ascii=False, indent=2)
                    return {"ok": True, "url": final_url, "title": "JSON Response",
                            "content": formatted[:limit], "chars": len(formatted[:limit])}
                except Exception:
                    pass

            html_text = content_bytes.decode(encoding, errors="replace")
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
            "--headless=new",
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


def browser_interact(url: str, actions: list[dict] | None = None, wait_ms: int = 2500,
                     screenshot: bool = True, max_chars: int = 25000, emit=None) -> dict:
    """多步无头浏览器交互与视觉快照执行引擎。"""
    from .browser_tools import browser_interact as _browser_interact
    return _browser_interact(
        url=url,
        actions=actions,
        wait_ms=wait_ms,
        screenshot=screenshot,
        max_chars=max_chars,
        emit=emit,
    )
