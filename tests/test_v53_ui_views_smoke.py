"""TASK-4.3: real-browser smoke test for all 14 navigation views.

The test boots ``tools/serve_ui_test.py`` (a disposable synthetic database,
dev-login enabled only inside that isolated process) and drives a real
headless Chromium through the fourteen navigation entries rendered by
``web/src/App.jsx``. For every view it asserts that the main region mounted,
that the navigation state settled on the clicked entry, and that the whole
run produced no ``pageerror``, no ``console.error`` and no failing API
response.

Screenshots and ``smoke-report.json`` go to ``$V53_UI_SMOKE_ARTIFACTS`` when
that variable is set, otherwise to ``%TEMP%/v53_ui_smoke/<timestamp>/``.

The suite skips (with an explicit reason) only when the browser tooling or the
built frontend is absent; it never skips individual views.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

REPO = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = REPO / "tools" / "serve_ui_test.py"
DIST_INDEX = REPO / "web" / "dist" / "index.html"
APP_JSX = REPO / "web" / "src" / "App.jsx"

# (key, navigation label) in the exact order App.jsx renders them.
EXPECTED_NAV = [
    ("cyberdeck", "赛博指挥舱"),
    ("overview", "工作台"),
    ("projects", "总装流水线"),
    ("tasks", "任务中心"),
    ("chats", "协作会话"),
    ("files", "工作产物"),
    ("approvals", "审批中心"),
    ("org", "组织与团队"),
    ("roles", "角色库"),
    ("finops", "用量与费用"),
    ("evals", "评测中心"),
    ("experience", "组织经验"),
    ("extensions", "工具与集成"),
    ("maintenance", "系统维护"),
]
ROLE_GATED = {"extensions", "maintenance"}

NAV_SELECTOR = 'nav[aria-label="主导航"]'
NAV_BUTTON_SELECTOR = NAV_SELECTOR + " > button"

# The SPA is served from /app/; Chromium still asks the origin root for a
# favicon that this sub-path deployment does not provide. Fulfilling that one
# implicit browser request keeps the "zero console errors" assertion strict
# instead of filtering it out afterwards.
FAVICON_ROUTE = "**/favicon.ico"

SERVER_START_TIMEOUT = 180.0
BROWSER_TIMEOUT_MS = 60_000
# Views mount first and may paint their data a few frames later; measure only
# after the mounted content has settled, but never fail on a legitimately
# short (e.g. empty-state) view.
VIEW_SETTLE_MS = 3_000
VIEW_SETTLE_MIN_TEXT = 20

MEASURE_VIEW_JS = """() => {
    const root = document.getElementById('root');
    const right = document.querySelector('.right');
    if (!root || !right) return null;
    const box = right.getBoundingClientRect();
    const children = [...right.children]
        .filter(el => !el.classList.contains('app-topbar') && !el.classList.contains('hint'))
        .map(el => {
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || '').trim();
            return {
                tag: el.tagName,
                cls: el.className,
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                text: text.length,
            };
        });
    const bodyText = document.body.innerText || '';
    return {
        rootChildren: root.children.length,
        right: {width: Math.round(box.width), height: Math.round(box.height)},
        rightText: (right.innerText || '').trim().length,
        children,
        bootError: bodyText.includes('工作空间暂时无法加载')
            || bodyText.includes('部分工作空间数据暂时不可用'),
    };
}"""


def _mounted_containers(state: dict | None) -> list[dict]:
    if not state:
        return []
    return [
        child for child in state["children"]
        if child["width"] > 100 and child["height"] > 100 and child["text"] >= 5
    ]


def _measure_settled_view(page) -> dict | None:
    """Measure the active view once its content container has text."""
    deadline = time.monotonic() + VIEW_SETTLE_MS / 1000
    state = page.evaluate(MEASURE_VIEW_JS)
    while time.monotonic() < deadline:
        mounted = _mounted_containers(state)
        if mounted and mounted[0]["text"] >= VIEW_SETTLE_MIN_TEXT:
            return state
        page.wait_for_timeout(100)
        state = page.evaluate(MEASURE_VIEW_JS)
    return state


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _cached_chromium() -> str:
    """Newest Chromium executable from the Playwright browser cache, if any."""
    roots = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "ms-playwright")
    roots.append(Path.home() / "AppData" / "Local" / "ms-playwright")
    roots.append(Path.home() / ".cache" / "ms-playwright")
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found += sorted(root.glob("chromium-*/chrome-win64/chrome.exe"))
            found += sorted(root.glob("chromium-*/chrome-win/chrome.exe"))
            found += sorted(root.glob("chromium-*/chrome-linux/chrome"))
    return str(found[-1]) if found else ""


def _launch_chromium(playwright):
    """Launch headless Chromium, tolerating a stale cached browser revision.

    Returns ``(browser, mode)``. Tries the Playwright-managed browser first,
    then a cached Chromium binary, then locally installed Chrome/Edge.
    """
    attempts: list[dict] = [{}, {"channel": "chrome"}, {"channel": "msedge"}]
    cached = _cached_chromium()
    if cached:
        attempts.insert(1, {"executable_path": cached})
    failures = []
    for options in attempts:
        if "executable_path" in options and not Path(options["executable_path"]).is_file():
            continue
        try:
            return playwright.chromium.launch(headless=True, **options), options
        except Exception as exc:  # noqa: BLE001 - report every attempt
            failures.append(f"{options or 'bundled'}: {str(exc).splitlines()[0][:200]}")
    pytest.skip("No usable Chromium for Playwright. Attempts: " + " | ".join(failures))


def _server_info_from_log(log_path: Path, proc: subprocess.Popen) -> dict:
    """Wait for tools/serve_ui_test.py to print its JSON startup line."""
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = ""
            if log_path.is_file():
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"UI test server exited early (rc={proc.returncode}). Log tail:\n{tail}")
        if log_path.is_file():
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "test_url" in payload:
                    return payload
        time.sleep(0.2)
    raise RuntimeError(f"UI test server did not report its URL within {SERVER_START_TIMEOUT:.0f}s")


def _wait_until_serving(base_url: str, log_path: Path, proc: subprocess.Popen) -> None:
    """Wait until the ASGI app answers HTTP (any status proves it is listening)."""
    deadline = time.monotonic() + SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
            raise RuntimeError(f"UI test server exited while starting (rc={proc.returncode}). Log tail:\n{tail}")
        try:
            urllib.request.urlopen(base_url + "/app/", timeout=2).close()
            return
        except urllib.error.HTTPError:
            return
        except Exception:  # noqa: BLE001 - not listening yet
            time.sleep(0.2)
    raise RuntimeError(f"UI test server never answered on {base_url}")


@pytest.fixture(scope="module")
def artifacts() -> Path:
    requested = os.environ.get("V53_UI_SMOKE_ARTIFACTS")
    root = Path(requested) if requested else Path(tempfile.gettempdir()) / "v53_ui_smoke"
    target = root / time.strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture(scope="module")
def ui_server(tmp_path_factory) -> dict:
    """Isolated UI server: fresh synthetic database, never the production DB."""
    if not SERVER_SCRIPT.is_file():
        pytest.skip(f"Missing {SERVER_SCRIPT}")
    if not DIST_INDEX.is_file():
        pytest.skip("web/dist is not built; run `npm --prefix web run build` first")

    data_dir = tmp_path_factory.mktemp("v53-ui-smoke-db-")
    log_dir = tmp_path_factory.mktemp("v53-ui-smoke-log-")
    log_path = log_dir / "server.log"
    port = _free_port()
    if port == 8077:
        port = _free_port()
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})

    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT), "--data-dir", str(data_dir), "--port", str(port)],
            cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT, env=env,
        )
        try:
            info = _server_info_from_log(log_path, proc)
            base_url = f"http://127.0.0.1:{port}"
            _wait_until_serving(base_url, log_path, proc)
            yield {
                "base_url": base_url,
                "identity_id": info["test_identity_id"],
                "data_dir": str(data_dir),
                "log_path": str(log_path),
                "pid": proc.pid,
                "runner": info.get("runner"),
            }
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=15)


@pytest.fixture(scope="module")
def chromium():
    playwright_module = pytest.importorskip(
        "playwright.sync_api", reason="Python Playwright is not installed in this interpreter"
    )
    playwright = playwright_module.sync_playwright().start()
    try:
        browser, mode = _launch_chromium(playwright)
    except BaseException:
        playwright.stop()
        raise
    try:
        yield {"browser": browser, "mode": mode, "version": browser.version}
    finally:
        browser.close()
        playwright.stop()


# ------------------------------------------------------------------ contract
def test_ui_nav_items_contract_matches_fourteen_views():
    """Static guard: 14 nav entries, stable keys, and a render branch per key.

    This one needs no browser, so the navigation contract is still covered on
    machines where the browser smoke test above skips.
    """
    source = APP_JSX.read_text(encoding="utf-8")
    block = re.search(r"const navItems = \[(.*?)\]\.filter\(", source, re.S)
    assert block, "App.jsx must declare `const navItems = [...] .filter(...)`"
    entries = re.findall(r'\[\s*"([a-z]+)",\s*"([a-z]+)",\s*"([^"]+)"\s*\]', block.group(1))
    assert len(entries) == 14, f"navItems must hold exactly 14 views, found {len(entries)}: {entries}"
    assert [(key, label) for key, _icon, label in entries] == EXPECTED_NAV
    keys = [key for key, _icon, _label in entries]
    assert len(set(keys)) == 14, f"duplicate nav keys: {keys}"

    # Every key must be reachable from the render switch, including role-gated
    # views and the chat layout that keys off `tab === "chats"`.
    for key in keys:
        assert f'tab === "{key}"' in source, f"no render branch for nav key {key!r}"

    assert '["ceo", "system"].includes(me.role)' in source, "extensions/maintenance role gate disappeared"
    gated = {key for key, _icon, _label in entries} & ROLE_GATED
    assert gated == ROLE_GATED
    for key in ROLE_GATED:
        assert f'key !== "{key}"' in source, f"{key} is no longer behind the admin role gate"


# --------------------------------------------------------------- browser E2E
def test_ui_fourteen_views_render_in_real_browser(ui_server, chromium, artifacts):
    """Click all 14 nav entries in a real browser and check each mounted view."""
    browser = chromium["browser"]
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    console_errors: list[str] = []
    page_errors: list[str] = []
    bad_responses: list[str] = []

    def on_console(message):
        if message.type == "error":
            console_errors.append(message.text)

    def on_pageerror(error):
        page_errors.append(str(error))

    def on_response(response):
        if response.status < 400:
            return
        path = urllib.parse.urlparse(response.url).path
        bad_responses.append(f"{response.request.method} {path} -> {response.status}")

    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("response", on_response)
    page.route(FAVICON_ROUTE, lambda route: route.fulfill(status=204, body=""))

    report: dict = {
        "server": ui_server,
        "browser": {"mode": chromium["mode"], "version": chromium["version"]},
        "views": [],
    }
    try:
        login_url = (
            f"{ui_server['base_url']}/ui/dev-login"
            f"?as_id={urllib.parse.quote(ui_server['identity_id'])}&tab=cyberdeck"
        )
        page.goto(login_url, wait_until="load", timeout=BROWSER_TIMEOUT_MS)
        page.wait_for_selector(NAV_SELECTOR, state="visible", timeout=BROWSER_TIMEOUT_MS)

        # The smoke run must be a deployment admin (ceo/system), otherwise the
        # role-gated views would be absent and 14/14 coverage impossible.
        identity = page.evaluate("fetch('/ui/me').then(r => r.json())")
        assert identity["identity_id"] == ui_server["identity_id"]
        assert identity["role"] in {"ceo", "system"}, f"dev-login identity is {identity['role']!r}, not an admin"
        report["identity"] = {"identity_id": identity["identity_id"], "role": identity["role"]}

        buttons = page.locator(NAV_BUTTON_SELECTOR)
        assert buttons.count() == 14, f"expected 14 nav items for ceo, found {buttons.count()}"
        rendered_labels = buttons.evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
        assert rendered_labels == [label for _key, label in EXPECTED_NAV], rendered_labels
        report["nav_count"] = buttons.count()

        nav = page.locator(NAV_SELECTOR)
        for index, (key, label) in enumerate(EXPECTED_NAV):
            nav.get_by_role("button", name=label, exact=True).click()
            page.wait_for_function(
                """(lbl) => {
                    const nav = document.querySelector('nav[aria-label="主导航"]');
                    const current = nav && nav.querySelector(':scope > button[aria-current="page"]');
                    return !!current && current.getAttribute('aria-label') === lbl;
                }""",
                arg=label,
                timeout=BROWSER_TIMEOUT_MS,
            )
            state = _measure_settled_view(page)
            assert state is not None, f"{key}: shell (.right / #root) disappeared"
            assert state["rootChildren"] > 0, f"{key}: #root is empty"
            assert state["right"]["width"] > 200 and state["right"]["height"] > 200, (
                f"{key}: main region collapsed to {state['right']}"
            )
            assert state["rightText"] > 20, f"{key}: main region has no text (white screen)"
            mounted = _mounted_containers(state)
            assert mounted, f"{key}: no mounted content container, children={state['children']}"
            assert not state["bootError"], f"{key}: workspace boot error banner is visible"

            shot = artifacts / f"{index:02d}-{key}.png"
            page.screenshot(path=str(shot), full_page=True, animations="disabled")
            report["views"].append({
                "key": key,
                "label": label,
                "aria_current": True,
                "container": mounted[0],
                "right_text_len": state["rightText"],
                "screenshot": str(shot),
            })

        assert buttons.count() == 14, "nav item count changed during the run"
        report["page_errors"] = page_errors
        report["console_errors"] = console_errors
        report["bad_responses"] = bad_responses
        (artifacts / "smoke-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        assert page_errors == [], f"pageerror during smoke run: {page_errors}"
        assert console_errors == [], f"console.error during smoke run: {console_errors}"
        assert bad_responses == [], f"failing responses during smoke run: {bad_responses}"
        print(
            f"[ui-smoke] browser={chromium['mode']} version={chromium['version']} "
            f"nav={report['nav_count']} views={len(report['views'])} "
            f"pageerrors=0 consoleerrors=0 badresponses=0 artifacts={artifacts}"
        )
    finally:
        page.close()
