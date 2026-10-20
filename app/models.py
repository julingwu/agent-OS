"""Request/response models for the six verbs and the task endpoints."""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_ENTITY = os.environ.get("DEFAULT_LEGAL_ENTITY_ID", "LE-GROUP-HQ")


class SpawnRunIn(BaseModel):
    task_id: str
    run_seq: int = Field(ge=1)
    legal_entity_id: str | None = None


class HeartbeatIn(BaseModel):
    run_id: str
    hb_seq: int = Field(ge=1)
    estimated_next_cost: float | None = None
    legal_entity_id: str | None = None


class AttachArtifactIn(BaseModel):
    run_id: str
    art_seq: int = Field(ge=1)
    file_path: str
    content: str | None = None
    legal_entity_id: str | None = None


class ArbitrationIn(BaseModel):
    run_id: str
    arb_seq: int = Field(ge=1)
    criterion_id: str
    legal_entity_id: str | None = None


ADAPTER_EVENTS = frozenset({"run_started", "run_succeeded", "run_failed", "run_cancelled"})


class EmitEventIn(BaseModel):
    run_id: str
    evt_seq: int = Field(ge=1)
    event_type: Literal["run_started", "run_succeeded", "run_failed", "run_cancelled"]
    payload: dict | None = None
    legal_entity_id: str | None = None


class ReserveBudgetIn(BaseModel):
    run_id: str
    call_seq: int = Field(ge=1)
    estimated_tokens: int = Field(ge=0)
    legal_entity_id: str | None = None


class FromSentenceIn(BaseModel):
    text: str
    legal_entity_id: str | None = None
    pack_id: str | None = None


class ManualCheckIn(BaseModel):
    passed: bool
    checked_by: str = "human"
    note: str | None = None
    evidence_snippet: str | None = None
    file_path: str | None = None
    line_no: int | None = None


class ExecuteRunIn(BaseModel):
    prompt: str | None = None
    runner: str | None = None
    budget_tokens: int | None = Field(default=None, ge=0)


class RetryIn(BaseModel):
    prompt: str | None = None
    runner: str | None = None
    budget_tokens: int | None = None


class DecisionIn(BaseModel):
    title: str
    summary: str | None = None
    task_id: str | None = None
    options: list | None = None
    recommendation: str | None = None
    amount: float = 0.0
    currency: str = "CNY"
    risk_level: str = "low"
    reversibility: str = "reversible"
    is_compliance: int = 0
    confidence: float = 1.0
    deadline: str | None = None
    legal_entity_id: str | None = None


class DecideIn(BaseModel):
    approved: bool
    decided_by: str = "ceo"
    note: str | None = None


class MemoryIn(BaseModel):
    scope: str
    content: str


class RelayIn(BaseModel):
    text: str
    coder_identity_id: str
    reviewer_identity_id: str
    runner: str | None = None
