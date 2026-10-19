"""E2E：像人一样点控制台。

覆盖：登录 → 会话列表 → 群聊 → 发一句闲聊（不应建任务）→ 收起/展开验收卡
      → 组织架构 → 文件库 → 设置 → 帮助 → 关于 → 群聊「⋯」菜单。

用真实 Chrome（machine 上已有），不下载 Playwright 自带的 Chromium。
    python tools/e2e_console.py            # headless
    python tools/e2e_console.py --headed   # 看得见
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys

from playwright.sync_api import expect, sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = os.environ.get("OA_BASE_URL", "http://127.0.0.1:8077")
CHROME = os.environ.get(
    "CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SHOTS = ROOT / "docs" / "e2e"
SHOTS.mkdir(parents=True, exist_ok=True)


def ceo_credentials() -> tuple[str, str]:
    c = sqlite3.connect(ROOT / "data" / "oa.db")
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT id, secret FROM identities WHERE role='ceo' AND state='active' LIMIT 1").fetchone()
    return row["id"], row["secret"]


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def run(headed: bool) -> int:
    ident, secret = ceo_credentials()
    steps: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, headless=not headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(20000)
        log("goto")
        page.goto(f"{BASE}/app/", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        # 1. 登录
        log("login")
        page.fill("input[placeholder='ID-xxxxxxxxxxxx']", ident)
        page.fill("input[placeholder='••••••••']", secret)
        page.click("button:has-text('进入控制台')")
        expect(page.locator(".brand")).to_be_visible(timeout=15000)
        steps.append("登录成功")
        page.screenshot(path=str(SHOTS / "01_login.png"))

        # 2. 会话列表 + 群聊
        log("chats")
        page.click(".tab:has-text('会话')")
        items = page.locator(".chat-item")
        expect(items.first).to_be_visible(timeout=10000)
        group = page.locator(".chat-item", has_text="双十一写作组")
        if group.count():
            group.first.click()
        else:
            items.first.click()
        steps.append(f"会话列表 {items.count()} 个")

        # 3. 闲聊：不应产生任务卡
        log("chat send")
        before_cards = page.locator(".card").count()
        before_agent = page.locator(".msg:not(.human)").count()
        page.fill("textarea", "你好，在吗")
        page.keyboard.press("Enter")
        expect(page.locator(".msg.human").last).to_be_visible(timeout=10000)
        # 关键：必须真的有人回话。只断言「没建任务」时，群路由整个挂掉也会"通过"（踩过）
        page.wait_for_function(
            f"document.querySelectorAll('.msg:not(.human)').length > {before_agent}",
            timeout=25000)
        after_cards = page.locator(".card").count()
        assert after_cards == before_cards, "闲聊不应该建任务"
        steps.append("闲聊：AI 有回复且未建任务")
        page.screenshot(path=str(SHOTS / "02_chat.png"))

        # 4. 验收卡可展开（有历史任务时）
        if page.locator(".card").count():
            page.locator(".card details summary").first.click()
            steps.append("验收卡展开")
            page.screenshot(path=str(SHOTS / "03_card.png"))

        # 5. 组织架构 + 员工面板
        log("org")
        page.click(".tab:has-text('组织架构')")
        page.locator(".tree-emp").first.click()
        expect(page.locator(".panel")).to_be_visible(timeout=8000)
        steps.append("员工详情面板")
        page.screenshot(path=str(SHOTS / "04_employee.png"))
        page.locator(".panel .icon-btn").click()

        # 6. 文件库
        log("files")
        page.click(".tab:has-text('文件库')")
        expect(page.locator(".file-row").first).to_be_visible(timeout=8000)
        steps.append(f"文件库 {page.locator('.file-row').count()} 个产物")
        page.screenshot(path=str(SHOTS / "05_files.png"))

        # 7. 设置（含定时任务/知识库）
        log("settings")
        page.click(".sidebar-footer .icon-btn[title='设置']")
        expect(page.locator(".modal h3", has_text="设置")).to_be_visible(timeout=8000)
        steps.append("设置面板")
        page.screenshot(path=str(SHOTS / "06_settings.png"))
        page.click(".modal button:has-text('关闭')")

        # 8. 帮助 / 关于
        log("help/about")
        page.click(".tab:has-text('会话')")
        page.click(".icon-btn[title='帮助']")
        expect(page.locator(".modal h3", has_text="怎么用")).to_be_visible(timeout=8000)
        steps.append("帮助")
        page.click(".modal button:has-text('知道了')")
        page.click(".icon-btn[title='关于']")
        expect(page.locator(".modal h3", has_text="关于")).to_be_visible(timeout=8000)
        steps.append("关于")
        page.screenshot(path=str(SHOTS / "07_about.png"))
        page.click(".modal button:has-text('关闭')")

        # 9. 群聊「⋯」菜单
        log("group menu")
        if group.count():
            group.first.click()
            page.click(".icon-btn[title='群聊设置']")
            expect(page.locator(".modal h3", has_text="群聊设置")).to_be_visible(timeout=8000)
            steps.append("群聊菜单（邀请/移出/解散）")
            page.screenshot(path=str(SHOTS / "08_group_menu.png"))
            page.click(".modal button:has-text('关闭')")

        # 10. 主题切换
        log("theme")
        page.click(".sidebar-footer .icon-btn[title='主题']")
        theme = page.evaluate("document.documentElement.dataset.theme")
        steps.append(f"主题切换 → {theme}")
        page.screenshot(path=str(SHOTS / "09_theme.png"))
        page.click(".sidebar-footer .icon-btn[title='主题']")

        browser.close()

    print(json.dumps({"ok": True, "steps": steps}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    sys.exit(run(ap.parse_args().headed))
