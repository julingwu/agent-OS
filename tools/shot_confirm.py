"""截图：项目制群聊的两阶段（确认卡 → 开发完成）。"""
import sqlite3
import sys

from playwright.sync_api import sync_playwright

ROOT = r"C:\oaagent-m0"
BASE = "http://127.0.0.1:8077"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def creds():
    c = sqlite3.connect(rf"{ROOT}\data\oa.db")
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT id, secret FROM identities WHERE role='ceo' AND state='active' LIMIT 1").fetchone()
    return r["id"], r["secret"]


def main():
    ident, secret = creds()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 960})
        pg.set_default_timeout(15000)
        pg.goto(f"{BASE}/app/", wait_until="domcontentloaded")
        pg.fill("input[placeholder='ID-xxxxxxxxxxxx']", ident)
        pg.fill("input[placeholder='••••••••']", secret)
        pg.click("button:has-text('进入控制台')")
        pg.wait_for_selector(".brand")
        pg.click(".tab:has-text('会话')")
        row = pg.locator(".chat-item").filter(has_text="项目组").first
        row.click()
        pg.wait_for_selector(".card.confirm", timeout=15000)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=rf"{ROOT}\docs\v12_confirm.png")
        print("saved docsv12_confirm.png")
        b.close()


main()
