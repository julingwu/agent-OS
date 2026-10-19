"""Test helpers: sign requests the same way tools/oa_client.py does."""
from __future__ import annotations

import json
import pathlib
import sys
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import identity  # noqa: E402


def body_of(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signed_headers(ident_id: str, secret: str, method: str, path: str,
                   params: dict | None, body: bytes, *, ts: str | None = None,
                   nonce: str | None = None) -> dict:
    query = identity.sorted_query(sorted((str(k), str(v)) for k, v in (params or {}).items()))
    ts = ts or str(int(time.time()))
    nonce = nonce or uuid.uuid4().hex
    sig = identity.sign(secret, method, path, query, ts, nonce, body)
    headers = {identity.HEADERS["key"]: ident_id, identity.HEADERS["ts"]: ts,
               identity.HEADERS["nonce"]: nonce, identity.HEADERS["sig"]: sig}
    if body:
        headers["content-type"] = "application/json"
    return headers


class SignedClient:
    """Drop-in for TestClient: same .get/.post signature, adds signature headers."""

    def __init__(self, tc, ident_id: str, secret: str, role: str = "system"):
        self._tc = tc
        self.ident_id = ident_id
        self.secret = secret
        self.role = role

    def request(self, method: str, url: str, json=None, params=None, headers=None, **kw):
        body = b"" if json is None else body_of(json)
        h = signed_headers(self.ident_id, self.secret, method, url, params, body)
        h.update(headers or {})
        return self._tc.request(method, url, content=body or None, params=params, headers=h, **kw)

    def get(self, url, params=None, **kw):
        return self.request("GET", url, params=params, **kw)

    def post(self, url, json=None, params=None, **kw):
        return self.request("POST", url, json=json, params=params, **kw)
