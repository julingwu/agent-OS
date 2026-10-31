"""Relay endpoint: one call dispatches coder -> reviewer."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException

from .. import tenant
from ..db import get_conn
from ..identity import require
from ..models import RelayIn
from .. import relay

router = APIRouter(tags=["relay"])


@router.post("/relay/run")
def run_relay(req: RelayIn, _ident: dict = Depends(require("task:*"))):
    with get_conn() as conn:
        try:
            return relay.run_relay(
                conn, text=req.text, coder_id=req.coder_identity_id,
                reviewer_id=req.reviewer_identity_id,
                entity=tenant.entity_of(_ident),
                runner=(req.runner or os.environ.get("RUNNER") or "mock").lower())
        except ValueError as exc:
            raise HTTPException(400, detail={"code": "BadRelay", "message": str(exc)})
