# Browser regression

Run these commands from the repository root with Node.js 24 and the Python
dependencies installed. Install the isolated Chromium runtime, build the UI, and
start the dedicated mock server:

```sh
npm --prefix web ci
npm --prefix web exec playwright install chromium
npm --prefix web run build
python tools/serve_ui_test.py --port 8078
```

The helper uses a fresh synthetic database and workspace, enables dev-login only
for its isolated process, disables model keys and external integrations, and
creates the required test identity and employee roles. Keep that server running.
In another terminal, use its printed `test_url` and `test_identity_id`:

```sh
AGENT_OS_TEST_URL=http://localhost:8078 AGENT_OS_TEST_ID=<test_identity_id> npm --prefix web run test:browser
```

PowerShell users can set these environment variables with `$env:NAME="value"`
before invoking `npm --prefix web run test:browser`. Set `AGENT_OS_TEST_ID` from
`test_identity_id`, `AGENT_OS_TEST_TENANT_ID` from `test_tenant_identity_id`, and
`AGENT_OS_TEST_SUBSIDIARY_ID` from `test_subsidiary_identity_id`. All three are required;
no local account is hardcoded. The URL is guarded: only HTTP localhost
with an explicit port is accepted, and port 8077 is prohibited. The suite also
checks the signed-in identity and refuses a backend with a configured model key.

`preview-security.spec.js` rotates only the isolated synthetic identity's credential,
then uses the normal login form to verify cookie and Bearer preview paths, short
single-artifact tickets, and blocked external resources. Run it only in this
disposable test environment, never against personal or shared accounts.

Tests cover 390, 768, and 1440 px layouts, workspace navigation, task-center
controls, role cards in the main region, mobile conversation selection, 150%
page zoom with reduced motion, modal Tab/Escape/focus restoration (including
inside file previews), keyboard Enter/Space attachment choosers, authorized
approval entry points and resumed/rejected status updates, and the complete
mock task lifecycle. A task-list
regression injects one expected HTTP 503 and delayed search responses to verify
retry behavior and protection against stale results. They create
synthetic content tasks and intentionally retain them in the test database to
verify state persistence after reload.

`integrations.spec.js` verifies experience review and re-review after edits,
retrieval excludes pending entries, MCP connection/binding changes, secret-free
configuration responses, SMTP configuration without sending mail, and responsive
integration forms. `extensions-bindings.spec.js` delays and fails synthetic
binding reads to verify that loading cannot clear a grant, saves lock the employee
selection, and stale responses cannot replace the current employee's grants.
`tenant-access.spec.js` exercises a second tenant's normal login form, reload,
organization/task isolation, optional department failure, and subsidiary CEO
denial for deployment-wide configuration changes.

Artifacts and failure traces go to the OS temporary directory. To save the three
workspace screenshots elsewhere, set `AGENT_OS_TEST_SCREENSHOT_DIR`; filenames
are `ui-390.png`, `ui-768.png`, and `ui-1440.png`. The real mock-task workflow
also saves `ui-task-inspector.png`, `ui-task-preview.png`, and populated
`ui-populated-1440.png` / `ui-populated-390.png`. Traces contain only synthetic
test data and should not be collected against a personal workspace.
