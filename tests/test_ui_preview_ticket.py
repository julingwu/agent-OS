"""A preview frame receives a short single-artifact capability, not a session."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn
from tests.test_ui_preview_embed import preview_client


@pytest.mark.parametrize("authentication", ["cookie", "bearer"])
def test_ticket_issuer_authenticates_normally_but_frame_never_needs_session(preview_client, authentication):
    client, artifact, session = preview_client
    artifact(".html", b"<!doctype html><h1>Standalone safe preview</h1>")
    from app.ui import admin, api
    client.app.include_router(api.router)
    issuer = TestClient(client.app)
    if authentication == "cookie":
        issuer.cookies.set("oa_ui", session)
    else:
        issuer.headers["authorization"] = f"Bearer {session}"
    response = issuer.post("/ui/files/A-PREVIEW/preview-ticket")
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    assert ticket != session and session not in ticket
    assert api._read_cookie(ticket) is None
    assert admin.PREVIEW_TICKET_TTL == 120
    anonymous = TestClient(client.app)
    frame = anonymous.get("/ui/files/A-PREVIEW/preview", params={"embed": 1, "ticket": ticket})
    assert frame.status_code == 200 and "Standalone safe preview" in frame.text
    assert session not in str(frame.url) and session not in frame.text
    assert "t" not in frame.url.params
    assert anonymous.get("/ui/tasks", params={"t": ticket}).status_code == 401
    assert anonymous.post("/ui/tasks/T-PREVIEW/action", headers={"authorization": f"Bearer {ticket}"},
                          json={"action": "accept"}).status_code == 401
    assert anonymous.get("/ui/files/A-PREVIEW/download", params={"t": ticket}).status_code == 401
    assert anonymous.post("/ui/files/A-PREVIEW/preview-ticket", headers={"authorization": f"Bearer {ticket}"}).status_code == 401
    assert anonymous.get("/ui/files/A-OTHER/preview", params={"ticket": ticket}).status_code == 401
    # Supplying a session in the frame URL is rejected even with valid cookies.
    assert issuer.get("/ui/files/A-PREVIEW/preview", params={"t": session}).status_code == 400
    assert anonymous.post("/ui/files/A-PREVIEW/preview-ticket", params={"t": session}).status_code == 401


def test_ticket_expires_and_cannot_be_tampered(preview_client, monkeypatch):
    client, artifact, _ = preview_client
    artifact(".txt", b"test preview")
    from app.ui import admin
    issued = client.post("/ui/files/A-PREVIEW/preview-ticket").json()
    anonymous = TestClient(client.app)
    ticket = issued["ticket"]
    damaged = ticket[:-1] + ("1" if ticket[-1] == "0" else "0")
    assert anonymous.get("/ui/files/A-PREVIEW/preview", params={"ticket": damaged}).status_code == 401
    monkeypatch.setattr(admin, "time", SimpleNamespace(time=lambda: issued["expires_at"]))
    expired = anonymous.get("/ui/files/A-PREVIEW/preview", params={"ticket": ticket})
    assert expired.status_code == 401 and expired.json()["detail"]["code"] == "InvalidPreviewTicket"


@pytest.mark.parametrize("change", ["revoked", "unknown", "tenant", "scope", "deleted"])
def test_ticket_rechecks_current_identity_scope_tenant_and_artifact(preview_client, change):
    client, artifact, session = preview_client
    artifact(".txt", b"test preview")
    from app.ui import api
    ticket = client.post("/ui/files/A-PREVIEW/preview-ticket").json()["ticket"]
    who = api._read_cookie(session)
    with get_conn() as conn:
        if change == "revoked":
            conn.execute("UPDATE identities SET state='revoked' WHERE id=?", (who,))
        elif change == "unknown":
            conn.execute("DELETE FROM identities WHERE id=?", (who,))
        elif change == "tenant":
            conn.execute("UPDATE identities SET legal_entity_id='LE-GROUP-HQ' WHERE id=?", (who,))
        elif change == "scope":
            conn.execute("UPDATE identities SET role='auditor',scopes='[]' WHERE id=?", (who,))
        else:
            conn.execute("UPDATE artifacts SET state='deleted' WHERE id='A-PREVIEW'")
    result = TestClient(client.app).get("/ui/files/A-PREVIEW/preview", params={"ticket": ticket})
    expected = 401 if change in ("revoked", "unknown") else 403 if change == "scope" else 404
    assert result.status_code == expected


@pytest.mark.parametrize("ticket", ["", "invalid", "artifact-preview.v1.x.y", "artifact-preview.v1." + "x" * 3000 + ".00"])
def test_malformed_tickets_fail_without_rendering_artifact(preview_client, ticket):
    client, artifact, _ = preview_client
    artifact(".txt", b"must not render")
    response = TestClient(client.app).get("/ui/files/A-PREVIEW/preview", params={"ticket": ticket})
    assert response.status_code == 401
    assert "must not render" not in response.text


def test_preview_csp_blocks_external_resources_and_preserves_inline_rendering(preview_client):
    client, artifact, _ = preview_client
    artifact(".html", b"<style>body{color:red}</style><script>window.inlineScript=true</script>")
    response = client.get("/ui/files/A-PREVIEW/preview?embed=1")
    assert response.status_code == 200
    policy = dict(part.strip().split(" ", 1) for part in response.headers["content-security-policy"].split(";"))
    assert policy["default-src"] == "'none'"
    assert policy["script-src"] == policy["style-src"] == "'unsafe-inline'"
    for kind in ("img-src", "font-src", "media-src"):
        assert set(policy[kind].split()) <= {"data:", "blob:"}
    for kind in ("connect-src", "frame-src", "object-src", "base-uri", "form-action"):
        assert policy[kind] == "'none'"
    assert policy["sandbox"] == "allow-scripts"
    assert "window.inlineScript=true" in response.text and "body{color:red}" in response.text
