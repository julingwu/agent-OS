"""截图：产物预览弹窗（v1.2 功能）。"""
import sqlite3
import sys
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(r"C:\oaagent-m0")
BASE = "http://127.0.0.1:8077"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def creds():
    c = sqlite3.connect(ROOT / "data" / "oa.db")
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT id, secret FROM identities WHERE role='ceo' AND state='active' LIMIT 1").fetchone()
    return r["id"], r["secret"]


def main() -> None:
    ident, secret = creds()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.set_default_timeout(15000)
        pg.goto(f"{BASE}/app/", wait_until="domcontentloaded")
        pg.fill("input[placeholder='ID-xxxxxxxxxxxx']", ident)
        pg.fill("input[placeholder='••••••••']", secret)
        pg.click("button:has-text('进入控制台')")
        pg.wait_for_selector(".brand", timeout=15000)
        pg.click(".tab:has-text('文件库')")
        pg.wait_for_selector(".file-row", timeout=10000)
        # 找到 html 产物的预览按钮
        row = pg.locator(".file-row", has_text=".html").first
        row.locator("button:has-text('预览')").click()
        pg.wait_for_selector(".preview-frame", timeout=10000)
        pg.wait_for_timeout(2500)
        out = ROOT / "docs" / "v12_preview.png"
        pg.screenshot(path=str(out))
        print("saved", out)
        b.close()


if __name__ == "__main__":
    sys.exit(main())
