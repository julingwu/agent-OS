# External tools and mail drafts

The integration takes the HTTP MCP connection and employee binding model from
`sync/v3.2`, with tenant, entity ownership and credential checks added. The
professional workspace remains the primary UI.

## HTTP MCP

An active `ceo` or `system` identity can manage tool connections in **工具与集成**.
Each connection belongs to the administrator's entity and tenant. A subsidiary
CEO cannot change a parent's connection. A parent may explicitly bind its own
connection to an employee in its permitted entity subtree. A system identity
still stays inside its own tenant.

Supported endpoints use the existing session cookie or Bearer authentication:

| Method | Path | Purpose |
| --- | --- | --- |
| GET / POST | `/ui/mcp` | List owned connections / create |
| PATCH / DELETE | `/ui/mcp/{id}` | Update / remove an owned connection |
| POST | `/ui/mcp/{id}/test` | Initialize and list tools |
| GET / PUT | `/ui/mcp/bindings/{employee_id}` | Read / replace owned bindings |
| GET / PUT | `/ui/smtp` | Read / save the caller's tenant and entity SMTP settings |
| POST | `/ui/smtp/test` | Explicitly submit one fixed test email to the entered recipient |

Create a connection with `name`, `url`, and optionally `api_key`, `auth_header`
and `auth_scheme` (defaults: `Authorization`, `Bearer`). Only HTTP transport is
supported. URLs cannot contain user credentials, query parameters or fragments;
credentials belong in the dedicated field. A changed URL clears the previous
key unless a replacement key is supplied in that same request. Probe requests
never follow redirects. Credentials stay in local SQLite, whose file access is
part of the deployment's trust boundary, and are omitted from API responses.

`PUT /ui/mcp/bindings/{employee_id}` accepts `{"server_ids":["MCP-…"]}`.
Binding changes are explicit grants of capability. Only enabled connections
within the employee's current tenant and entity scope enter ACP `session/new`.
The runner checks a stored configuration fingerprint before resuming an existing
session and before dispatching a prompt. Changed or revoked permissions require
a new session.

Treat the configured MCP server as trusted software. External MCP tools execute
inside DSH. Notification-based cancel guards cannot guarantee that a remote
side effect has not already occurred; role prompts are guidance, not an
authorization boundary. This release does not claim per-tool dispatch approval
or mid-call revocation for arbitrary external servers.

## Mail preparation gateway

The bundled gateway exposes **`prepare_email`**, which creates local `.eml`
drafts. It does not expose `send_email`, connect to SMTP or deliver customer mail.
Automatic delivery remains deferred until exact-payload approval, durable
dispatch state and reconciliation of ambiguous delivery attempts are available.

Run the gateway manually in the Python environment with:

```powershell
$env:MCP_MAIL_TENANT = 'TENANT-HQ'
# Set MCP_EMAIL_TOKEN securely in this process environment; minimum 16 characters.
# Optional: point MCP_MAIL_DIR at a dedicated draft output directory.
python tools/mcp_email_server.py --host 127.0.0.1 --port 8099
```

Register `http://127.0.0.1:8099/mcp` in **工具与集成** with that token, test the
connection, then bind it to the intended employee. The gateway is not started or
bound automatically.

Attachments are disabled unless the operator scopes the gateway using either:

- `MCP_MAIL_TASK_ID` plus `DB_PATH`: only active registered artifacts from that
  fixed task's latest run and fixed tenant; file checksums must still match.
- `MCP_MAIL_ARTIFACT_DIR`: a dedicated artifact directory. True resolved path
  containment excludes sibling-prefix tricks and symlinks escaping the directory.

The repository root, environment files, databases, keys and hidden/configuration
directories are not accepted attachment sources. Total attachment size is capped
at 10 MB. Changing task or artifact directory scope requires restarting the
operator-managed gateway. Do not grant a shared broad artifact directory.

The tool takes `to`, `subject`, `body` and optional absolute attachment paths.
It returns `delivery_status: "saved"` and `delivered: false`. The exact tenant,
task, recipient, subject, body and attachment checksums produce a stable draft
identifier; a local manifest records that payload. Repeating the same request
reuses the local draft only after its saved file hash is verified. Draft and
manifest files use complete temporary writes and atomic replacement, and a
process-safe lock protects concurrent saves. Missing or interrupted files are
repaired on retry. This is draft deduplication, not a promise of exactly-once
external delivery.

## SMTP settings and tests

SMTP passwords are stored separately for each tenant and entity, never returned
by the API. Omitting `password` preserves it only when host, port and user remain
unchanged. An empty password explicitly clears it. Host/account changes clear
old credentials. The explicit test action sends a fixed test message and reports
submission or failure; a failure is never presented as successful delivery.

Automated tests use temporary databases, loopback HTTP servers and fake SMTP.
They do not send external mail or call a real model.
