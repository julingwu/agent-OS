"""Request signing (HMAC-SHA256) and identity verification.

Canonical string, joined by newlines:

    METHOD
    PATH
    sorted-query
    timestamp
    nonce
    sha256-hex(body)

Everything an attacker could vary is covered: method, path, query, body. The
timestamp bounds replay in time, the nonce bounds it to one use.

Secrets are stored in the database because HMAC verification needs the key
itself (a hash of the key cannot verify a MAC). The DB is therefore part of the
trust boundary; see DESIGN_GAPS §20.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from typing import Iterable

from fastapi import HTTPException, Request

from .audit import new_id, write_audit

TS_WINDOW = int(300)          # seconds a signature stays valid
HEADERS = {"key": "X-OA-Key", "ts": "X-OA-Ts", "nonce": "X-OA-Nonce", "sig": "X-OA-Sig"}

ROLE_SCOPES = {
    "system": {"*"},
    "ceo": {"task:*", "verb:*", "accept:judge", "decision:*", "audit:read",
            "decision:decide_ceo", "memory:read", "memory:write"},
    "manager": {"task:*", "verb:*", "accept:judge", "decision:create", "decision:evaluate",
                "decision:read", "decision:decide_manager", "audit:read",
                "memory:read", "memory:write"},
    "agent": {"task:create", "task:read", "verb:*", "decision:create", "decision:evaluate",
              "decision:read", "memory:read", "memory:write"},
}


# ------------------------------------------------------------------ identities
def issue(conn: sqlite3.Connection, *, name: str, role: str,
          legal_entity_id: str | None = None, employee_id: str | None = None) -> dict:
    if role not in ROLE_SCOPES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(ROLE_SCOPES)}")
    ident_id = new_id("ID")
    secret = secrets.token_hex(32)
    conn.execute(
        """INSERT INTO identities (id, name, role, legal_entity_id, employee_id, secret, scopes, state)
           VALUES (?,?,?,?,?,?,?, 'active')""",
        (ident_id, name, role, legal_entity_id or "LE-GROUP-HQ", employee_id, secret,
         json.dumps(sorted(ROLE_SCOPES[role]))),
    )
    write_audit(conn, legal_entity_id=legal_entity_id or "LE-GROUP-HQ",
                verb="identity_issued", actor="system", action="identity_issued",
                target=ident_id, payload={"name": name, "role": role},
                response={"state": "active"})
    return {"id": ident_id, "name": name, "role": role, "secret": secret}


def revoke(conn: sqlite3.Connection, ident_id: str) -> None:
    conn.execute("UPDATE identities SET state='revoked' WHERE id = ?", (ident_id,))


def get_identity(conn: sqlite3.Connection, ident_id: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM identities WHERE id = ?", (ident_id,)).fetchone()
    if row is None or row["state"] != "active":
        return None
    return row


def scopes_of(row: sqlite3.Row) -> set[str]:
    stored = json.loads(row["scopes"] or "[]")
    return set(stored) | ROLE_SCOPES.get(row["role"], set())


def has_scope(row: sqlite3.Row, needed: str) -> bool:
    scopes = scopes_of(row)
    if "*" in scopes or needed in scopes:
        return True
    prefix = needed.split(":")[0] + ":*"
    return prefix in scopes


# ------------------------------------------------------------------ signing
def sorted_query(items: Iterable[tuple[str, str]]) -> str:
    return "&".join(f"{k}={v}" for k, v in sorted(items))


def canonical(method: str, path: str, query: str, ts: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, query, ts, nonce,
                      hashlib.sha256(body).hexdigest()])


def sign(secret: str, method: str, path: str, query: str, ts: str, nonce: str,
         body: bytes) -> str:
    msg = canonical(method, path, query, ts, nonce, body).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ verification
def verify(conn: sqlite3.Connection, request: Request, body: bytes) -> sqlite3.Row:
    key = request.headers.get(HEADERS["key"], "")
    ts = request.headers.get(HEADERS["ts"], "")
    nonce = request.headers.get(HEADERS["nonce"], "")
    sig = request.headers.get(HEADERS["sig"], "")
    if not all([key, ts, nonce, sig]):
        raise HTTPException(401, detail={"code": "MissingSignature",
                                         "message": "X-OA-Key/Ts/Nonce/Sig are required"})

    row = get_identity(conn, key)
    if row is None:
        raise HTTPException(401, detail={"code": "UnknownIdentity", "id": key})

    try:
        skew = abs(int(time.time()) - int(ts))
    except ValueError:
        raise HTTPException(401, detail={"code": "BadTimestamp"})
    if skew > TS_WINDOW:
        raise HTTPException(401, detail={"code": "SignatureExpired", "skew_seconds": skew})

    query = sorted_query(request.query_params.multi_items())
    expected = sign(row["secret"], request.method, request.url.path, query, ts, nonce, body)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, detail={"code": "BadSignature"})

    try:
        conn.execute("INSERT INTO auth_nonces (nonce, identity_id, ts) VALUES (?,?,?)",
                     (nonce, key, int(ts)))
    except sqlite3.IntegrityError:
        raise HTTPException(401, detail={"code": "ReplayDetected", "nonce": nonce})
    return row


def require(scope: str):
    """FastAPI dependency: verify the signature, then require a scope."""
    async def dependency(request: Request):
        from .db import get_conn

        body = await request.body()
        with get_conn() as conn:
            row = verify(conn, request, body)
            if not has_scope(dict(row), scope):
                raise HTTPException(403, detail={"code": "Forbidden", "scope": scope,
                                                 "role": row["role"]})
            return dict(row)
    return dependency
