"""Signed HTTP client for the OA shell.

Every request carries X-OA-Key/Ts/Nonce/Sig; the body is serialized once so the
bytes that get hashed are the bytes that get sent.

    from oa_client import client_from_env
    C = client_from_env()
    C.post("/tasks/from_sentence", json={"text": "..."})
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import identity  # noqa: E402
from app.config import load_env  # noqa: E402


def dumps(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class OAClient:
    def __init__(self, base_url: str, ident_id: str, secret: str,
                 timeout: float = 900.0, transport=None):
        self.base_url = base_url.rstrip("/")
        self.ident_id = ident_id
        self.secret = secret
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout,
                                  trust_env=False, transport=transport)

    def headers(self, method: str, path: str, params: dict | None, body: bytes) -> dict:
        query = identity.sorted_query(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        ts, nonce = str(int(time.time())), uuid.uuid4().hex
        sig = identity.sign(self.secret, method, path, query, ts, nonce, body)
        h = {identity.HEADERS["key"]: self.ident_id, identity.HEADERS["ts"]: ts,
             identity.HEADERS["nonce"]: nonce, identity.HEADERS["sig"]: sig}
        if body:
            h["content-type"] = "application/json"
        return h

    def request(self, method: str, path: str, *, json_body=None, params=None, **kw):
        body = b"" if json_body is None else dumps(json_body)
        headers = self.headers(method, path, params, body)
        return self._http.request(method, path, content=body or None, params=params,
                                  headers=headers, **kw)

    def get(self, path, params=None, **kw):
        return self.request("GET", path, params=params, **kw)

    def post(self, path, json=None, params=None, **kw):
        return self.request("POST", path, json_body=json, params=params, **kw)


def client_from_env() -> OAClient:
    load_env()                       # scripts get the operator key from .env
    ident = os.environ.get("OA_ID", "")
    secret = os.environ.get("OA_SECRET", "")
    if not ident or not secret:
        raise SystemExit("OA_ID / OA_SECRET are not set (run tools/auth_admin.py issue)")
    return OAClient(os.environ.get("OA_BASE", "http://127.0.0.1:8077"), ident, secret)
