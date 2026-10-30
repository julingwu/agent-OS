"""Multi-step headless browser interaction and visual snapshot execution engine.

Supports dynamic Web SPA workflows: navigation, clicking, form typing, scrolling,
waiting, CSS-targeted DOM extraction, and artifact screenshot capture.
Gracefully degrades to static DOM parsing when headless browser binaries are unavailable.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import logging
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

import httpx
from bs4 import BeautifulSoup

from .. import artifacts
from .web_tools import (
    DEFAULT_USER_AGENT,
    HEADERS,
    _find_browser_binary,
    _html_to_markdown,
)

log = logging.getLogger("app.agent.browser_tools")

SUPPORTED_ACTIONS = {"navigate", "click", "type", "scroll", "wait", "extract", "screenshot"}


def _has_playwright() -> bool:
    """Check if playwright library is available."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _generate_degraded_screenshot_bytes(url: str, title: str) -> bytes:
    """Generate a clean visual fallback image when browser screenshot is unavailable."""
    try:
        from PIL import Image, ImageDraw

        width, height = 960, 540
        img = Image.new("RGB", (width, height), color=(245, 247, 250))
        draw = ImageDraw.Draw(img)

        # Card container
        draw.rectangle([(20, 20), (width - 20, height - 20)], fill=(255, 255, 255), outline=(220, 226, 235), width=2)
        # Header bar
        draw.rectangle([(20, 20), (width - 20, 75)], fill=(238, 242, 246))
        draw.text((40, 38), "AgentOS Browser Engine - Visual Snapshot (Degraded Mode)", fill=(30, 41, 59))

        # Metadata
        clean_url = (url or "")[:80]
        clean_title = (title or "")[:70]
        draw.text((40, 110), f"URL: {clean_url}", fill=(71, 85, 105))
        draw.text((40, 150), f"Title: {clean_title}", fill=(71, 85, 105))
        draw.text(
            (40, 210),
            "Status: Headless Chromium binary unavailable; fell back to static DOM capture.",
            fill=(148, 163, 184),
        )
        draw.text(
            (40, 240),
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            fill=(148, 163, 184),
        )

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:
        log.warning("PIL fallback image generation failed: %s", exc)
        return (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?"
            b"\x00\x05\xfe\x02\xfe\r\xef5\xcb\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def _clean_html_to_markdown(soup: BeautifulSoup, base_url: str = "") -> str:
    """Convert HTML soup to Markdown, falling back to body text if standard semantic tags are absent."""
    clean_soup = BeautifulSoup(str(soup), "html.parser")
    md = _html_to_markdown(clean_soup, base_url=base_url)
    if not md.strip():
        fallback_soup = BeautifulSoup(str(soup), "html.parser")
        for tag in fallback_soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()
        body = fallback_soup.find("body") or fallback_soup
        text = body.get_text("\n\n", strip=True)
        md = re.sub(r"\n{3,}", "\n\n", text).strip()
    return md


def _load_static_content(raw_url: str) -> tuple[str, str]:
    """Fetch or load raw HTML content for static degradation."""
    if raw_url.startswith("data:text/html"):
        idx = raw_url.find(",")
        raw_part = raw_url[idx + 1 :] if idx != -1 else ""
        html_text = urllib.parse.unquote(raw_part)
        return html_text, raw_url

    if raw_url.startswith("file://"):
        file_path = urllib.request.url2pathname(urllib.parse.urlparse(raw_url).path)
        if os.name == "nt" and file_path.startswith("\\") and len(file_path) > 2 and file_path[2] == ":":
            file_path = file_path[1:]
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), raw_url

    with httpx.Client(timeout=10.0, follow_redirects=True, trust_env=False, headers=HEADERS) as client:
        resp = client.get(raw_url)
        return resp.text, str(resp.url)


def _run_degraded(
    raw_url: str,
    actions: list[dict],
    wait_ms: int = 2500,
    screenshot: bool = True,
    max_chars: int = 25000,
    emit: Callable | None = None,
    fallback_reason: str = "",
) -> dict:
    """Graceful degradation when headless browser is missing or fails to launch."""
    actions_executed: list[dict] = []
    extracted_items: list[dict] = []
    screenshot_art: dict | None = None

    current_url = raw_url
    try:
        html_text, current_url = _load_static_content(raw_url)
    except Exception as exc:
        log.warning("Degraded static fetch failed for %s: %s", raw_url, exc)
        return {
            "ok": False,
            "error": f"网页抓取失败: {exc}",
            "url": raw_url,
            "title": "",
            "actions_executed": [],
            "content": f"> **[降级执行异常]** 抓取页面失败: {exc}",
            "screenshot": None,
            "total_chars": 0,
            "chars": 0,
            "degraded": True,
        }

    soup = BeautifulSoup(html_text, "html.parser")
    t_tag = soup.find("title")
    title = t_tag.get_text(strip=True) if t_tag else current_url

    for act in actions:
        if not isinstance(act, dict):
            actions_executed.append({
                "action": "unknown",
                "status": "failed",
                "error": "动作必须为字典对象",
            })
            continue

        act_type = str(act.get("action", "")).strip().lower()
        if act_type not in SUPPORTED_ACTIONS:
            actions_executed.append({
                "action": act_type or "unknown",
                "status": "failed",
                "error": f"不支持的动作类型: {act_type}",
            })
            continue

        if act_type == "navigate":
            target_url = str(act.get("url", "")).strip()
            if not target_url:
                actions_executed.append({"action": "navigate", "status": "failed", "error": "缺少必要参数 url"})
            else:
                try:
                    html_text, current_url = _load_static_content(target_url)
                    soup = BeautifulSoup(html_text, "html.parser")
                    t_tag = soup.find("title")
                    title = t_tag.get_text(strip=True) if t_tag else current_url
                    actions_executed.append({
                        "action": "navigate",
                        "url": current_url,
                        "status": "ok",
                        "title": title,
                        "detail": "静态降级重定向获取成功",
                    })
                except Exception as e:
                    actions_executed.append({"action": "navigate", "url": target_url, "status": "failed", "error": str(e)})

        elif act_type == "extract":
            selector = act.get("selector")
            if not selector or not isinstance(selector, str):
                actions_executed.append({"action": "extract", "status": "failed", "error": "缺少必要参数 selector"})
            else:
                try:
                    matches = soup.select(selector)
                    if matches:
                        ex_text = "\n".join(m.get_text(strip=True) for m in matches if m.get_text(strip=True))
                        extracted_items.append({"selector": selector, "text": ex_text})
                        actions_executed.append({"action": "extract", "selector": selector, "status": "ok", "extracted": ex_text})
                    else:
                        actions_executed.append({"action": "extract", "selector": selector, "status": "failed", "error": f"未找到匹配元素: {selector}"})
                except Exception as e:
                    actions_executed.append({"action": "extract", "selector": selector, "status": "failed", "error": str(e)})

        elif act_type == "wait":
            try:
                ms = max(50, min(int(act.get("ms", 1000) or 1000), 5000))
            except (ValueError, TypeError):
                ms = 1000
            time.sleep(ms / 1000.0)
            actions_executed.append({"action": "wait", "ms": ms, "status": "ok", "detail": "降级模式延时"})

        elif act_type in ("click", "type", "scroll"):
            actions_executed.append({
                "action": act_type,
                "status": "skipped",
                "detail": f"静态降级模式：无可用无头浏览器，跳过动态交互动作 ({act_type})",
            })

        elif act_type == "screenshot":
            png_bytes = _generate_degraded_screenshot_bytes(current_url, title)
            try:
                art = artifacts.save_bytes(
                    f"browser_snapshot_{int(time.time())}.png",
                    png_bytes,
                    kind="image/png",
                )
                screenshot_art = {
                    "id": art["id"],
                    "url": art["url"],
                    "name": art["name"],
                    "bytes": art["bytes"],
                    "markdown": f"![browser_snapshot]({art['url']})",
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
                actions_executed.append({
                    "action": "screenshot",
                    "status": "ok",
                    "artifact_id": art["id"],
                    "url": art["url"],
                    "markdown": f"![browser_snapshot]({art['url']})",
                })
            except Exception as e:
                actions_executed.append({"action": "screenshot", "status": "failed", "error": str(e)})

    # Final screenshot in degraded mode if requested
    if screenshot and screenshot_art is None:
        try:
            png_bytes = _generate_degraded_screenshot_bytes(current_url, title)
            art = artifacts.save_bytes(
                f"browser_snapshot_{int(time.time())}.png",
                png_bytes,
                kind="image/png",
            )
            screenshot_art = {
                "id": art["id"],
                "url": art["url"],
                "name": art["name"],
                "bytes": art["bytes"],
                "markdown": f"![browser_snapshot]({art['url']})",
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
        except Exception as e:
            log.warning("Degraded screenshot save failed: %s", e)

    # Convert DOM to markdown using clean robust converter
    markdown_body = _clean_html_to_markdown(BeautifulSoup(html_text, "html.parser"), base_url=current_url)

    reason = fallback_reason or "系统未检测到可用无头浏览器，已自动优雅降级为静态 HTTP DOM 提取。"
    degrade_notice = (
        f"> [!NOTE]\n"
        f"> **[降级运行提示]** {reason}\n"
        f"> 动态交互动作（click / type / scroll）已安全跳过，静态内容与定向提取正常保留。\n\n"
    )

    content = degrade_notice
    if extracted_items:
        ex_lines = ["### 🎯 定向提取内容 (Extracted Content)\n"]
        for item in extracted_items:
            ex_lines.append(f"- **Selector `{item['selector']}`**:\n```text\n{item['text']}\n```\n")
        content += "\n".join(ex_lines) + "\n---\n\n"

    content += markdown_body

    limit = max(100, min(int(max_chars or 25000), 100000))
    if len(content) > limit:
        content = content[:limit] + "\n\n...[正文过长，已截断]"

    if screenshot_art and screenshot_art.get("markdown") and screenshot_art["markdown"] not in content:
        content = content + f"\n\n{screenshot_art['markdown']}"

    return {
        "ok": True,
        "url": current_url,
        "title": title or current_url,
        "actions_executed": actions_executed,
        "content": content,
        "screenshot": screenshot_art,
        "total_chars": len(content),
        "chars": len(content),
        "degraded": True,
    }


def _run_playwright_interact(
    raw_url: str,
    actions: list[dict],
    wait_ms: int,
    screenshot: bool,
    max_chars: int,
    emit: Callable | None,
    browser_bin: str | None,
) -> dict:
    """Run interactive multi-step actions inside headless Chromium via Playwright."""
    from playwright.sync_api import sync_playwright

    actions_executed: list[dict] = []
    extracted_items: list[dict] = []
    screenshot_art: dict | None = None

    launch_kwargs: dict[str, Any] = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
        ],
    }
    if browser_bin:
        launch_kwargs["executable_path"] = browser_bin

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=DEFAULT_USER_AGENT,
            )
            page = context.new_page()
            page.set_default_timeout(10000)

            # 1. Initial navigation
            try:
                page.goto(raw_url, wait_until="domcontentloaded", timeout=max(5000, wait_ms * 2))
                if wait_ms > 0:
                    page.wait_for_timeout(min(wait_ms, 10000))
            except Exception as e:
                log.warning("Initial goto failed for %s: %s", raw_url, e)

            # 2. Iterate actions
            for act in actions:
                if not isinstance(act, dict):
                    actions_executed.append({
                        "action": "unknown",
                        "status": "failed",
                        "error": "动作必须为字典对象",
                    })
                    continue

                act_type = str(act.get("action", "")).strip().lower()
                if act_type not in SUPPORTED_ACTIONS:
                    actions_executed.append({
                        "action": act_type or "unknown",
                        "status": "failed",
                        "error": f"不支持的动作类型: {act_type}",
                    })
                    continue

                if act_type == "navigate":
                    target_url = str(act.get("url", "")).strip()
                    if not target_url:
                        actions_executed.append({"action": "navigate", "status": "failed", "error": "缺少必要参数 url"})
                    else:
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                            page.wait_for_timeout(500)
                            actions_executed.append({
                                "action": "navigate",
                                "url": target_url,
                                "status": "ok",
                                "title": page.title(),
                            })
                        except Exception as e:
                            actions_executed.append({"action": "navigate", "url": target_url, "status": "failed", "error": str(e)})

                elif act_type == "click":
                    selector = act.get("selector")
                    if not selector or not isinstance(selector, str):
                        actions_executed.append({"action": "click", "status": "failed", "error": "缺少必要参数 selector"})
                    else:
                        try:
                            page.wait_for_selector(selector, timeout=5000)
                            page.click(selector, timeout=5000)
                            page.wait_for_timeout(500)
                            actions_executed.append({"action": "click", "selector": selector, "status": "ok"})
                        except Exception as e:
                            actions_executed.append({"action": "click", "selector": selector, "status": "failed", "error": str(e)})

                elif act_type == "type":
                    selector = act.get("selector")
                    text = str(act.get("text", ""))
                    if not selector or not isinstance(selector, str):
                        actions_executed.append({"action": "type", "status": "failed", "error": "缺少必要参数 selector"})
                    else:
                        try:
                            page.wait_for_selector(selector, timeout=5000)
                            page.fill(selector, text, timeout=5000)
                            actions_executed.append({"action": "type", "selector": selector, "text": text, "status": "ok"})
                        except Exception as e:
                            actions_executed.append({"action": "type", "selector": selector, "status": "failed", "error": str(e)})

                elif act_type == "scroll":
                    direction = str(act.get("direction", "down")).lower()
                    try:
                        amount = int(act.get("amount", 800) or 800)
                    except (ValueError, TypeError):
                        amount = 800
                    try:
                        if direction == "bottom":
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        elif direction == "top":
                            page.evaluate("window.scrollTo(0, 0)")
                        elif direction == "up":
                            page.evaluate(f"window.scrollBy(0, -{amount})")
                        else:  # down
                            page.evaluate(f"window.scrollBy(0, {amount})")
                        page.wait_for_timeout(300)
                        actions_executed.append({"action": "scroll", "direction": direction, "amount": amount, "status": "ok"})
                    except Exception as e:
                        actions_executed.append({"action": "scroll", "direction": direction, "amount": amount, "status": "failed", "error": str(e)})

                elif act_type == "wait":
                    try:
                        ms = max(50, min(int(act.get("ms", 1000) or 1000), 30000))
                    except (ValueError, TypeError):
                        ms = 1000
                    try:
                        page.wait_for_timeout(ms)
                        actions_executed.append({"action": "wait", "ms": ms, "status": "ok"})
                    except Exception as e:
                        actions_executed.append({"action": "wait", "ms": ms, "status": "failed", "error": str(e)})

                elif act_type == "extract":
                    selector = act.get("selector")
                    if not selector or not isinstance(selector, str):
                        actions_executed.append({"action": "extract", "status": "failed", "error": "缺少必要参数 selector"})
                    else:
                        try:
                            page.wait_for_selector(selector, timeout=5000)
                            els = page.query_selector_all(selector)
                            if els:
                                texts = [el.inner_text().strip() for el in els if el.inner_text().strip()]
                                ex_text = "\n\n".join(texts)
                                extracted_items.append({"selector": selector, "text": ex_text})
                                actions_executed.append({"action": "extract", "selector": selector, "status": "ok", "extracted": ex_text})
                            else:
                                actions_executed.append({"action": "extract", "selector": selector, "status": "failed", "error": "元素未找到"})
                        except Exception as e:
                            actions_executed.append({"action": "extract", "selector": selector, "status": "failed", "error": str(e)})

                elif act_type == "screenshot":
                    try:
                        shot_bytes = page.screenshot(full_page=True)
                        art = artifacts.save_bytes(
                            f"browser_snapshot_{int(time.time())}.png",
                            shot_bytes,
                            kind="image/png",
                        )
                        screenshot_art = {
                            "id": art["id"],
                            "url": art["url"],
                            "name": art["name"],
                            "bytes": art["bytes"],
                            "markdown": f"![browser_snapshot]({art['url']})",
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
                        actions_executed.append({
                            "action": "screenshot",
                            "status": "ok",
                            "artifact_id": art["id"],
                            "url": art["url"],
                            "markdown": f"![browser_snapshot]({art['url']})",
                        })
                    except Exception as e:
                        actions_executed.append({"action": "screenshot", "status": "failed", "error": str(e)})

            # Final screenshot if requested and not captured yet
            if screenshot and screenshot_art is None:
                try:
                    shot_bytes = page.screenshot(full_page=True)
                    art = artifacts.save_bytes(
                        f"browser_snapshot_{int(time.time())}.png",
                        shot_bytes,
                        kind="image/png",
                    )
                    screenshot_art = {
                        "id": art["id"],
                        "url": art["url"],
                        "name": art["name"],
                        "bytes": art["bytes"],
                        "markdown": f"![browser_snapshot]({art['url']})",
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
                except Exception as e:
                    log.warning("Final screenshot capture failed: %s", e)

            # DOM content extraction
            title = page.title() or ""
            current_url = page.url or raw_url
            dom_html = page.content() or ""
            soup = BeautifulSoup(dom_html, "html.parser")
            if not title:
                t_tag = soup.find("title")
                title = t_tag.get_text(strip=True) if t_tag else current_url

            content = _clean_html_to_markdown(BeautifulSoup(dom_html, "html.parser"), base_url=current_url)

            if extracted_items:
                ex_lines = ["### 🎯 定向提取内容 (Extracted Content)\n"]
                for item in extracted_items:
                    ex_lines.append(f"- **Selector `{item['selector']}`**:\n```text\n{item['text']}\n```\n")
                content = "\n".join(ex_lines) + "\n---\n\n" + content

            limit = max(100, min(int(max_chars or 25000), 100000))
            if len(content) > limit:
                content = content[:limit] + "\n\n...[正文过长，已截断]"

            if screenshot_art and screenshot_art.get("markdown") and screenshot_art["markdown"] not in content:
                content = content + f"\n\n{screenshot_art['markdown']}"

            return {
                "ok": True,
                "url": current_url,
                "title": title or current_url,
                "actions_executed": actions_executed,
                "content": content,
                "screenshot": screenshot_art,
                "total_chars": len(content),
                "chars": len(content),
                "degraded": False,
            }
        finally:
            try:
                browser.close()
            except Exception:
                pass


def browser_interact(
    url: str,
    actions: list[dict] | None = None,
    wait_ms: int = 2500,
    screenshot: bool = True,
    max_chars: int = 25000,
    emit: Callable | None = None,
) -> dict:
    """Execute multi-step browser interactions and capture visual snapshots.

    Parameters:
        url: Initial page URL (HTTP/HTTPS, or local file:// / data:).
        actions: Sequence of structured actions:
            - {"action": "navigate", "url": "..."}
            - {"action": "click", "selector": "#btn"}
            - {"action": "type", "selector": "input[name=q]", "text": "keyword"}
            - {"action": "scroll", "direction": "down", "amount": 800}
            - {"action": "wait", "ms": 1500}
            - {"action": "extract", "selector": ".content"}
            - {"action": "screenshot"}
        wait_ms: Initial page wait time in milliseconds (default 2500).
        screenshot: Whether to capture full snapshot artifact (default True).
        max_chars: Maximum characters of Markdown content (default 25000).
        emit: Event callback for broadcasting artifacts (e.g. emit("artifact.attached", ...)).

    Returns:
        dict: {ok, title, url, actions_executed, content, screenshot, total_chars, chars, degraded, ...}
    """
    raw_url = (url or "").strip()
    if not raw_url:
        return {
            "ok": False,
            "error": "URL 不能为空",
            "url": "",
            "title": "",
            "actions_executed": [],
            "content": "",
            "screenshot": None,
            "total_chars": 0,
            "chars": 0,
            "degraded": False,
        }

    if not raw_url.startswith(("http://", "https://", "file://", "data:", "about:")):
        if "://" not in raw_url and not raw_url.startswith(("about:", "data:")):
            raw_url = "https://" + raw_url
        else:
            return {
                "ok": False,
                "error": f"不支持的 URL 格式或协议: {raw_url}",
                "url": raw_url,
                "title": "",
                "actions_executed": [],
                "content": "",
                "screenshot": None,
                "total_chars": 0,
                "chars": 0,
                "degraded": False,
            }

    # Normalize actions
    if actions is None:
        norm_actions = []
    elif isinstance(actions, list):
        norm_actions = actions
    elif isinstance(actions, dict):
        norm_actions = [actions]
    else:
        norm_actions = []

    # Check for browser binary and playwright
    browser_bin = _find_browser_binary()
    has_playwright = _has_playwright()

    if has_playwright and browser_bin:
        # Execute via worker thread to avoid blocking or colliding with any caller asyncio event loop
        timeout_s = max(30, int(wait_ms / 1000) * 2 + len(norm_actions) * 10)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    _run_playwright_interact,
                    raw_url,
                    actions=norm_actions,
                    wait_ms=wait_ms,
                    screenshot=screenshot,
                    max_chars=max_chars,
                    emit=emit,
                    browser_bin=browser_bin,
                )
                return future.result(timeout=timeout_s)
        except Exception as exc:
            log.warning("Playwright interaction failed (%s), falling back to degraded mode", exc)
            return _run_degraded(
                raw_url,
                actions=norm_actions,
                wait_ms=wait_ms,
                screenshot=screenshot,
                max_chars=max_chars,
                emit=emit,
                fallback_reason=f"无头浏览器启动/执行受阻 ({exc})，已自动切换为静态提取",
            )

    # Graceful degradation fallback
    reason = "系统未检测到 Chromium/Chrome 二进制" if not browser_bin else "系统未安装 Playwright 库"
    return _run_degraded(
        raw_url,
        actions=norm_actions,
        wait_ms=wait_ms,
        screenshot=screenshot,
        max_chars=max_chars,
        emit=emit,
        fallback_reason=reason,
    )
