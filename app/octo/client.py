"""Octo Bot API client (send + long-poll events) and the human-token group API.

Bot tokens (bf_…) can send messages and read events, but they may **not** add
other bots to a group — that is a hard server-side rule. Group creation for the
PM agent therefore goes through the operator's human token.
"""
from __future__ import annotations

import os

import httpx


def _base() -> str:
    return os.environ.get("OCTO_API_URL", "http://127.0.0.1:28080").rstrip("/")


class OctoBotClient:
    def __init__(self, bot_token: str, base: str | None = None, timeout: float = 70.0) -> None:
        self.token = bot_token
        self.base = (base or _base()).rstrip("/")
        self._http = httpx.Client(base_url=self.base, timeout=timeout, trust_env=False)

    # ------------------------------------------------------------------ events
    def poll(self, timeout_ms: int = 25_000) -> list[dict]:
        r = self._http.post("/v1/bot/events",
                            headers={"Authorization": f"Bearer {self.token}"},
                            json={"timeout": timeout_ms})
        if r.status_code >= 400:
            raise RuntimeError(f"events failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        if isinstance(data, dict):
            for key in ("events", "data", "list"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data or []

    def ack(self, event_id) -> None:
        try:
            self._http.post(f"/v1/bot/events/{event_id}/ack",
                            headers={"Authorization": f"Bearer {self.token}"}, json={})
        except Exception:                        # noqa: BLE001 - ack is best-effort
            pass

    # ------------------------------------------------------------------ send
    def send(self, channel_id: str, channel_type: int, text: str) -> dict:
        r = self._http.post("/v1/bot/sendMessage",
                            headers={"Authorization": f"Bearer {self.token}"},
                            json={"channel_id": channel_id, "channel_type": channel_type,
                                  "payload": {"type": 1, "content": text}})
        if r.status_code >= 400:
            raise RuntimeError(f"send failed: {r.status_code} {r.text[:200]}")
        return r.json()

    def register(self) -> dict:
        r = self._http.post("/v1/bot/register",
                            headers={"Authorization": f"Bearer {self.token}"}, json={})
        return r.json()


class OctoUserClient:
    """Operator (human) surface: create groups and add bot members."""

    def __init__(self, token: str, base: str | None = None, timeout: float = 30.0) -> None:
        self.token = token
        self.base = (base or _base()).rstrip("/")
        self._http = httpx.Client(base_url=self.base, timeout=timeout, trust_env=False)

    def _headers(self) -> dict:
        return {"token": self.token}

    def create_group(self, name: str, members: list[str], space_id: str | None = None) -> dict:
        body = {"name": name, "members": members}
        sid = space_id or os.environ.get("OCTO_SPACE_ID", "")
        if sid:
            body["space_id"] = sid
        r = self._http.post("/v1/group/create", headers=self._headers(), json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"create_group failed: {r.status_code} {r.text[:200]}")
        data = r.json()
        return data.get("data", data)

    def add_members(self, group_no: str, members: list[str]) -> dict:
        r = self._http.post(f"/v1/groups/{group_no}/members", headers=self._headers(),
                            json={"members": members})
        if r.status_code >= 400:
            raise RuntimeError(f"add_members failed: {r.status_code} {r.text[:200]}")
        return r.json()

    def me(self) -> dict:
        r = self._http.post("/v1/user/login", json={})   # placeholder, unused
        return r.json()
