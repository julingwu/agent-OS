"""WeCom outbound client: access_token cache, text + template-card sends.

`WECOM_MOCK=1` swaps the transport for a local outbox table so the whole chain
(create → run → card → click → API) can be exercised without a corp account.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time

import httpx

from ..audit import new_id

log = logging.getLogger("wecom.client")
API = "https://qyapi.weixin.qq.com"


def _mock() -> bool:
    return os.environ.get("WECOM_MOCK", "0") == "1"


class WeComClient:
    def __init__(self, conn_factory) -> None:
        self._conn = conn_factory
        self._token: str | None = None
        self._token_at = 0.0
        self._http = httpx.Client(base_url=API, timeout=15, trust_env=False)

    # ---------------------------------------------------------------- token
    def _access_token(self) -> str:
        if _mock():
            return "mock-token"
        if self._token and time.time() - self._token_at < 6000:
            return self._token
        corp = os.environ["WECOM_CORP_ID"]
        secret = os.environ["WECOM_SECRET"]
        r = self._http.get("/cgi-bin/gettoken", params={"corpid": corp, "corpsecret": secret})
        data = r.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"gettoken failed: {data}")
        self._token = data["access_token"]
        self._token_at = time.time()
        return self._token

    # ---------------------------------------------------------------- sends
    def _record(self, to_user: str, msgtype: str, body: dict) -> str:
        msg_id = "MOCK-" + new_id("M")[2:]
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO wecom_outbox (id, to_user, msgtype, body)
                   VALUES (?,?,?,?)""",
                (msg_id, to_user, msgtype, json.dumps(body, ensure_ascii=False)))
            conn.commit()
        log.info("mock send %s -> %s (%s)", msg_id, to_user, msgtype)
        return msg_id

    def send(self, to_user: str, msgtype: str, payload: dict) -> str:
        if _mock() or os.environ.get("WECOM_DRY_RUN") == "1":
            return self._record(to_user, msgtype, payload)
        body = {"touser": to_user, "msgtype": msgtype, "agentid": int(os.environ["WECOM_AGENT_ID"]),
                msgtype: payload}
        r = self._http.post("/cgi-bin/message/send",
                            params={"access_token": self._access_token()}, json=body)
        data = r.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"send failed: {data}")
        return str(data.get("msgid", ""))

    def send_text(self, to_user: str, content: str) -> str:
        return self.send(to_user, "text", {"content": content[:2000]})

    def send_card(self, to_user: str, card: dict) -> str:
        return self.send(to_user, "template_card", card)
