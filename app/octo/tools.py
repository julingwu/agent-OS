"""Tools the PM agent may call. Each one is a thin, auditable wrapper.

Only the operator (human) token may create a group or pull bots in — the bot API
refuses bot→bot invitations. Everything else speaks as the bot.
"""
from __future__ import annotations

import os
import sqlite3

from . import client as octo_client


def human_token() -> str:
    return os.environ.get("OCTO_HUMAN_TOKEN", "")


def bot_client(conn: sqlite3.Connection, bot_uid: str) -> octo_client.OctoBotClient:
    row = conn.execute("SELECT bot_token FROM octo_bots WHERE bot_uid = ?", (bot_uid,)).fetchone()
    if row is None or not row["bot_token"]:
        raise RuntimeError(f"no bot token for {bot_uid}")
    return octo_client.OctoBotClient(row["bot_token"])


def create_group(name: str, members: list[str]) -> dict:
    """Create a group owned by the operator and pull the given uids in (humans + bots)."""
    tok = human_token()
    if not tok:
        raise RuntimeError("OCTO_HUMAN_TOKEN is not configured")
    api = octo_client.OctoUserClient(tok)
    return api.create_group(name, members)


def invite(group_no: str, members: list[str]) -> dict:
    tok = human_token()
    if not tok:
        raise RuntimeError("OCTO_HUMAN_TOKEN is not configured")
    return octo_client.OctoUserClient(tok).add_members(group_no, members)


def say(conn: sqlite3.Connection, bot_uid: str, channel_id: str, channel_type: int,
        text: str) -> dict:
    return bot_client(conn, bot_uid).send(channel_id, channel_type, text)
