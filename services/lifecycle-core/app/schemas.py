from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StudyCreate(BaseModel):
    title: str
    description: str = ""
    owner: str


class ProjectCreate(BaseModel):
    study_id: str
    name: str
    description: str = ""
    owner: str


class TransitionRequest(BaseModel):
    action: str
    reason: str | None = None


class EvidenceCreate(BaseModel):
    kind: str
    object_key: str
    sha256: str | None = None


class ApprovalCreate(BaseModel):
    kind: str
    decision: Literal["approved", "rejected"]
    decided_by: str
    rationale: str
    expires_at: str | None = None


class AssessmentCreate(BaseModel):
    version: str = "2026-11-v1"
    answers: dict[str, Any]


class Resources(BaseModel):
    cpu_vcpu: float = 0
    memory_gib: float = 0
    gpu_count: int = 0
    storage_gib: float = 0
    vdi_hours: float = 0
    licences: int = 0


class QuoteCreate(BaseModel):
    rate_card_version: str = "v1"
    resources: Resources
    hours: float = Field(gt=0)
    valid_days: int = 14


class QuoteAccept(BaseModel):
    rationale: str | None = None


class BudgetCreate(BaseModel):
    quote_id: str
    committed_amount: float = Field(gt=0)


class LedgerEntryCreate(BaseModel):
    entry_type: str
    amount: float
    reference: str
    budget_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    resource: dict[str, Any] | None = None


class AllocationCreate(BaseModel):
    cpu_vcpu: float = Field(gt=0)
    memory_gib: float = Field(gt=0)
    gpu_count: int = 0
    storage_gib: float = 0
    rationale: str = ""


class DataRequestCreate(BaseModel):
    direction: Literal["ingress", "egress"]
    dataset_name: str
    classification: str
    owner: str
    criteria: list[str] = Field(default_factory=list)
    rationale: str = ""


class DataDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    decided_by: str
    rationale: str
    criteria: list[str] = Field(default_factory=list)
