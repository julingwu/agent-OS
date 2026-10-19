import sqlite3, sys
from playwright.sync_api import sync_playwright
ROOT=r"C:\oaagent-m0"; BASE="http://127.0.0.1:8077"; CHROME=r"C:\Program Files\Google\Chrome\Application\chrome.exe"
c=sqlite3.connect(rf"{ROOT}\data\oa.db"); c.row_factory=sqlite3.Row
r=c.execute("SELECT id,secret FROM identities WHERE role='ceo' AND state='active' LIMIT 1").fetchone()
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=CHROME, headless=True)
    pg=b.new_page(viewport={"width":1440,"height":960}); pg.set_default_timeout(15000)
    pg.goto(f"{BASE}/app/", wait_until="domcontentloaded")
    pg.fill("input[placeholder='ID-xxxxxxxxxxxx']", r["id"]); pg.fill("input[placeholder='••••••••']", r["secret"])
    pg.click("button:has-text('进入控制台')"); pg.wait_for_selector(".brand")
    pg.click(".tab:has-text('会话')")
    pg.locator(".chat-item").filter(has_text="项目组").first.click()
    pg.wait_for_selector(".card", timeout=15000); pg.wait_for_timeout(1500)
    pg.screenshot(path=rf"{ROOT}\docs\v12_phase2.png"); print("saved v12_phase2.png")
    b.close()
