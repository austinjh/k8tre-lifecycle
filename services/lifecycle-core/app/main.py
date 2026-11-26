from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from .assessment import evaluate, template as assessment_template
from .auth import Actor, require_actor
from .db import (
    allocations,
    approvals,
    assessments,
    audit_events,
    budgets,
    capacity_snapshots,
    data_requests,
    evidence,
    get_engine,
    init_db,
    ledger_entries,
    outbox_events,
    projects,
    quotes,
    rate_cards,
    studies,
    usage_records,
)
from .rate_card import estimate, load_rate_card
from .schemas import (
    AllocationCreate,
    ApprovalCreate,
    AssessmentCreate,
    BudgetCreate,
    DataDecision,
    DataRequestCreate,
    EvidenceCreate,
    LedgerEntryCreate,
    ProjectCreate,
    QuoteAccept,
    QuoteCreate,
    StudyCreate,
    TransitionRequest,
)
from .state_machine import assert_transition


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="K8TRE Lifecycle Core",
    version="0.1.0",
    description="API-first research lifecycle, governance, finance and allocation service.",
    lifespan=lifespan,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_in(days: int) -> str:
    return (_now() + timedelta(days=days)).isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    return value


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


def _require_roles(actor: Actor, *roles: str) -> None:
    if not set(actor.roles).intersection(roles):
        raise HTTPException(
            status_code=403,
            detail=f"Requires one of roles: {', '.join(roles)}",
        )


def get_conn() -> Iterator[Connection]:
    # Write endpoints call conn.commit() before returning: a yield dependency's
    # cleanup runs after the response is sent, which would let a fast follow-up
    # request read stale state.
    with get_engine().connect() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _get_project(conn: Connection, project_id: str) -> Any:
    row = conn.execute(select(projects).where(projects.c.id == project_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return row


def _exists(conn: Connection, table: Any, *conditions: Any) -> bool:
    return conn.execute(select(func.count()).select_from(table).where(*conditions)).scalar_one() > 0


def record_event(
    conn: Connection,
    aggregate_type: str,
    aggregate_id: str,
    action: str,
    actor: Actor,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
) -> str:
    event_id = str(uuid.uuid4())
    payload = {
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "action": action,
        "actor": actor.username,
        "before": _jsonable(before),
        "after": _jsonable(after),
        "reason": reason,
    }
    conn.execute(
        audit_events.insert().values(
            id=event_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            action=action,
            actor=actor.username,
            actor_roles_json=list(actor.roles),
            before_json=_jsonable(before),
            after_json=_jsonable(after),
            reason=reason,
        )
    )
    conn.execute(
        outbox_events.insert().values(
            id=str(uuid.uuid4()),
            topic=f"{aggregate_type}.{action}",
            payload_json=_jsonable(payload),
            status="PENDING",
        )
    )
    return event_id


def check_transition_requirements(
    conn: Connection, project_id: str, action: str, reason: str | None
) -> None:
    if action == "submit":
        project = _get_project(conn, project_id)
        if not project.owner or not project.description:
            raise HTTPException(status_code=400, detail="Project owner and description are required")
    elif action == "request_governance":
        if not _exists(
            conn,
            assessments,
            assessments.c.project_id == project_id,
            assessments.c.status == "COMPLETE",
        ):
            raise HTTPException(status_code=409, detail="A completed assessment is required")
    elif action == "approve_governance":
        if not _exists(
            conn,
            approvals,
            approvals.c.project_id == project_id,
            approvals.c.kind == "governance",
            approvals.c.decision == "approved",
        ):
            raise HTTPException(status_code=409, detail="An approved governance decision is required")
    elif action == "approve_budget":
        quote = conn.execute(
            select(quotes).where(
                quotes.c.project_id == project_id,
                quotes.c.status == "accepted",
            )
        ).first()
        if not quote:
            raise HTTPException(status_code=409, detail="An accepted quote is required")
        if datetime.fromisoformat(str(quote.valid_until)) < _now():
            raise HTTPException(status_code=409, detail="The accepted quote has expired")
        if not _exists(
            conn,
            budgets,
            budgets.c.project_id == project_id,
            budgets.c.status == "COMMITTED",
        ):
            raise HTTPException(status_code=409, detail="A committed budget is required")
    elif action == "provision":
        if not _exists(
            conn,
            allocations,
            allocations.c.project_id == project_id,
            allocations.c.status == "RESERVED",
        ):
            raise HTTPException(status_code=409, detail="A reserved allocation is required")
    elif action == "activate":
        if not _exists(
            conn,
            evidence,
            evidence.c.project_id == project_id,
            evidence.c.kind == "provisioning",
        ):
            raise HTTPException(status_code=409, detail="Provisioning evidence is required")
    elif action == "suspend":
        if not reason:
            raise HTTPException(status_code=400, detail="A reason is required to suspend a project")
    elif action == "archive":
        if not _exists(
            conn,
            evidence,
            evidence.c.project_id == project_id,
            evidence.c.kind == "retention",
        ):
            raise HTTPException(status_code=409, detail="A retention decision is required")


@app.get("/healthz")
def healthz(conn: Connection = Depends(get_conn)) -> dict[str, str]:
    conn.execute(select(func.count()).select_from(projects))
    return {"status": "ok", "service": "lifecycle-core"}


# --- Rate card -------------------------------------------------------------


@app.get("/v1/rate-card")
def get_rate_card(version: str = Query(default="v1")) -> dict[str, Any]:
    rates = load_rate_card(version)
    return {
        "version": version,
        "currency": "GBP",
        "rates": rates,
        "note": "Simulated internal recharge rates for the prototype.",
    }


@app.post("/v1/quotes/estimate")
def quote_estimate(body: QuoteCreate) -> dict[str, Any]:
    """Price a resource profile without persisting a quote."""
    rates = load_rate_card(body.rate_card_version)
    lines = estimate(body.resources.model_dump(), body.hours, rates)
    return {"rate_card_version": body.rate_card_version, "currency": "GBP", "lines": lines}


# --- Studies and projects -------------------------------------------------


@app.post("/v1/studies", status_code=201)
def create_study(
    body: StudyCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    study_id = str(uuid.uuid4())
    payload = {"id": study_id, **body.model_dump()}
    conn.execute(studies.insert().values(**payload))
    record_event(conn, "study", study_id, "created", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/studies/{study_id}")
def get_study(study_id: str, conn: Connection = Depends(get_conn)) -> dict[str, Any]:
    row = conn.execute(select(studies).where(studies.c.id == study_id)).first()
    if not row:
        raise HTTPException(status_code=404, detail="Study not found")
    return _row(row) or {}


@app.post("/v1/projects", status_code=201)
def create_project(
    body: ProjectCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    if not _exists(conn, studies, studies.c.id == body.study_id):
        raise HTTPException(status_code=404, detail="Study not found")
    project_id = str(uuid.uuid4())
    payload = {
        "id": project_id,
        "study_id": body.study_id,
        "name": body.name,
        "description": body.description,
        "owner": body.owner,
        "state": "DRAFT",
        "control_tier": None,
    }
    conn.execute(projects.insert().values(**payload))
    record_event(conn, "project", project_id, "created", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/projects")
def list_projects(
    state: str | None = Query(default=None),
    conn: Connection = Depends(get_conn),
) -> list[dict[str, Any]]:
    query = select(projects)
    if state:
        query = query.where(projects.c.state == state)
    return [_row(row) or {} for row in conn.execute(query.order_by(projects.c.name))]


@app.get("/v1/projects/{project_id}")
def get_project(project_id: str, conn: Connection = Depends(get_conn)) -> dict[str, Any]:
    return _row(_get_project(conn, project_id)) or {}


@app.post("/v1/projects/{project_id}/transitions")
def transition_project(
    project_id: str,
    body: TransitionRequest,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    project = _get_project(conn, project_id)
    transition = assert_transition(str(project.state), body.action, actor.roles)
    check_transition_requirements(conn, project_id, body.action, body.reason)
    conn.execute(
        update(projects)
        .where(projects.c.id == project_id)
        .values(state=transition.target, updated_at=_now())
    )
    record_event(
        conn,
        "project",
        project_id,
        body.action,
        actor,
        before={"state": project.state},
        after={"state": transition.target},
        reason=body.reason,
    )
    conn.commit()
    return {"id": project_id, "state": transition.target}


@app.get("/v1/projects/{project_id}/audit")
def project_audit(
    project_id: str, conn: Connection = Depends(get_conn)
) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(audit_events)
        .where(audit_events.c.aggregate_id == project_id)
        .order_by(audit_events.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.get("/v1/projects/{project_id}/summary")
def project_summary(project_id: str, conn: Connection = Depends(get_conn)) -> dict[str, Any]:
    project = _get_project(conn, project_id)
    assessment = conn.execute(
        select(assessments)
        .where(assessments.c.project_id == project_id)
        .order_by(assessments.c.created_at.desc())
    ).first()
    budget = conn.execute(
        select(budgets)
        .where(budgets.c.project_id == project_id)
        .order_by(budgets.c.created_at.desc())
    ).first()
    consumed = conn.execute(
        select(func.coalesce(func.sum(ledger_entries.c.amount), 0)).where(
            ledger_entries.c.project_id == project_id,
            ledger_entries.c.entry_type == "charge",
        )
    ).scalar_one()
    committed = Decimal(str(budget.committed_amount)) if budget else Decimal("0")
    consumed_dec = Decimal(str(consumed))
    utilisation = float(consumed_dec / committed) if committed else 0.0
    return {
        "project": _row(project),
        "assessment": _row(assessment),
        "budget": _row(budget),
        "consumed_amount": float(consumed_dec),
        "utilisation": round(utilisation, 4),
        "alert": "BREACHED" if utilisation >= 1 else "WARNING" if utilisation >= 0.8 else "OK",
    }


# --- Evidence, approvals and assessments ---------------------------------


@app.post("/v1/projects/{project_id}/evidence", status_code=201)
def create_evidence(
    project_id: str,
    body: EvidenceCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _get_project(conn, project_id)
    evidence_id = str(uuid.uuid4())
    payload = {
        "id": evidence_id,
        "project_id": project_id,
        "kind": body.kind,
        "object_key": body.object_key,
        "sha256": body.sha256,
        "created_by": actor.username,
    }
    conn.execute(evidence.insert().values(**payload))
    record_event(conn, "evidence", evidence_id, "created", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/projects/{project_id}/evidence")
def list_evidence(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(evidence).where(evidence.c.project_id == project_id).order_by(evidence.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.post("/v1/projects/{project_id}/approvals", status_code=201)
def create_approval(
    project_id: str,
    body: ApprovalCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "dpo", "data_owner", "finance")
    _get_project(conn, project_id)
    approval_id = str(uuid.uuid4())
    payload = {
        "id": approval_id,
        "project_id": project_id,
        "kind": body.kind,
        "decision": body.decision,
        "decided_by": body.decided_by,
        "rationale": body.rationale,
        "expires_at": body.expires_at,
    }
    conn.execute(approvals.insert().values(**payload))
    record_event(conn, "approval", approval_id, body.decision, actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/assessment-template")
def get_assessment_template() -> dict[str, Any]:
    return assessment_template()


@app.post("/v1/projects/{project_id}/assessments", status_code=201)
def create_assessment(
    project_id: str,
    body: AssessmentCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "researcher", "project_manager", "dpo", "tre_operator")
    _get_project(conn, project_id)
    result = evaluate(body.version, body.answers)
    assessment_id = str(uuid.uuid4())
    payload = {
        "id": assessment_id,
        "project_id": project_id,
        "version": body.version,
        "answers_json": result["answers"],
        "mitigations_json": result["mitigations"],
        "evidence_required_json": result["evidence_required"],
        "risk_score": result["risk_score"],
        "classification": result["classification"],
        "control_tier": result["control_tier"],
        "status": "COMPLETE",
        "created_by": actor.username,
    }
    conn.execute(assessments.insert().values(**payload))
    conn.execute(
        update(projects)
        .where(projects.c.id == project_id)
        .values(control_tier=result["control_tier"])
    )
    record_event(conn, "assessment", assessment_id, "completed", actor, after=payload)
    conn.commit()
    return {**payload, "mitigations": result["mitigations"], "evidence_required": result["evidence_required"]}


@app.get("/v1/projects/{project_id}/assessments")
def list_assessments(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(assessments)
        .where(assessments.c.project_id == project_id)
        .order_by(assessments.c.created_at)
    )
    return [_row(row) or {} for row in rows]


# --- Finance --------------------------------------------------------------


@app.post("/v1/projects/{project_id}/quotes", status_code=201)
def create_quote(
    project_id: str,
    body: QuoteCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "finance")
    _get_project(conn, project_id)
    rates = load_rate_card(body.rate_card_version)
    if not _exists(conn, rate_cards, rate_cards.c.version == body.rate_card_version):
        conn.execute(
            rate_cards.insert().values(
                version=body.rate_card_version,
                rates_json=rates,
                effective_from="2026-11-26",
            )
        )
    lines = estimate(body.resources.model_dump(), body.hours, rates)
    quote_id = str(uuid.uuid4())
    payload = {
        "id": quote_id,
        "project_id": project_id,
        "rate_card_version": body.rate_card_version,
        "estimate_json": _jsonable(lines),
        "total_amount": lines["total"],
        "currency": "GBP",
        "valid_until": _iso_in(body.valid_days),
        "status": "DRAFT",
        "created_by": actor.username,
    }
    conn.execute(quotes.insert().values(**payload))
    record_event(conn, "quote", quote_id, "created", actor, after=payload)
    conn.commit()
    return payload


@app.post("/v1/quotes/{quote_id}/accept")
def accept_quote(
    quote_id: str,
    body: QuoteAccept,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "finance", "project_manager")
    quote = conn.execute(select(quotes).where(quotes.c.id == quote_id)).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")
    if datetime.fromisoformat(str(quote.valid_until)) < _now():
        raise HTTPException(status_code=409, detail="Quote has expired")
    conn.execute(update(quotes).where(quotes.c.id == quote_id).values(status="accepted"))
    record_event(
        conn,
        "quote",
        quote_id,
        "accepted",
        actor,
        before={"status": quote.status},
        after={"status": "accepted"},
        reason=body.rationale,
    )
    conn.commit()
    return {"id": quote_id, "status": "accepted"}


@app.get("/v1/projects/{project_id}/quotes")
def list_quotes(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(quotes).where(quotes.c.project_id == project_id).order_by(quotes.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.post("/v1/projects/{project_id}/budgets", status_code=201)
def create_budget(
    project_id: str,
    body: BudgetCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "finance")
    quote = conn.execute(
        select(quotes).where(quotes.c.id == body.quote_id, quotes.c.project_id == project_id)
    ).first()
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found for project")
    if Decimal(str(body.committed_amount)) > Decimal(str(quote.total_amount)):
        raise HTTPException(status_code=400, detail="Committed amount exceeds quote total")
    budget_id = str(uuid.uuid4())
    payload = {
        "id": budget_id,
        "project_id": project_id,
        "quote_id": body.quote_id,
        "committed_amount": body.committed_amount,
        "currency": "GBP",
        "status": "COMMITTED",
        "created_by": actor.username,
    }
    conn.execute(budgets.insert().values(**payload))
    record_event(conn, "budget", budget_id, "committed", actor, after=payload)
    conn.commit()
    return payload


@app.post("/v1/projects/{project_id}/ledger-entries", status_code=201)
def create_ledger_entry(
    project_id: str,
    body: LedgerEntryCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "finance", "tre_operator")
    _get_project(conn, project_id)
    entry_id = str(uuid.uuid4())
    payload = {
        "id": entry_id,
        "project_id": project_id,
        "budget_id": body.budget_id,
        "entry_type": body.entry_type,
        "amount": body.amount,
        "currency": "GBP",
        "reference": body.reference,
        "period_start": body.period_start,
        "period_end": body.period_end,
        "resource_json": _jsonable(body.resource),
    }
    conn.execute(ledger_entries.insert().values(**payload))
    record_event(conn, "ledger_entry", entry_id, "posted", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/projects/{project_id}/ledger")
def list_ledger(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(ledger_entries)
        .where(ledger_entries.c.project_id == project_id)
        .order_by(ledger_entries.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.post("/v1/projects/{project_id}/usage-records", status_code=201)
def create_usage_record(
    project_id: str,
    body: dict[str, Any],
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _get_project(conn, project_id)
    record_id = str(uuid.uuid4())
    payload = {
        "id": record_id,
        "project_id": project_id,
        "period_start": body["period_start"],
        "period_end": body["period_end"],
        "cpu_request_vcpu_hours": body.get("cpu_request_vcpu_hours", 0),
        "memory_request_gib_hours": body.get("memory_request_gib_hours", 0),
        "gpu_hours": body.get("gpu_hours", 0),
        "node_capacity_json": _jsonable(body.get("node_capacity", {})),
        "rate_card_version": body.get("rate_card_version", "v1"),
        "amount": body.get("amount", 0),
    }
    conn.execute(usage_records.insert().values(**payload))
    record_event(conn, "usage_record", record_id, "recorded", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/projects/{project_id}/usage")
def list_usage(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(usage_records)
        .where(usage_records.c.project_id == project_id)
        .order_by(usage_records.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.post("/v1/capacity-snapshots", status_code=201)
def create_capacity_snapshot(
    body: dict[str, Any],
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    payload = {
        "id": snapshot_id,
        "node_name": body["node_name"],
        "cpu_capacity_vcpu": body["cpu_capacity_vcpu"],
        "memory_capacity_gib": body["memory_capacity_gib"],
        "gpu_count": body.get("gpu_count", 0),
        "captured_at": body.get("captured_at", _now().isoformat()),
    }
    conn.execute(capacity_snapshots.insert().values(**payload))
    record_event(conn, "capacity_snapshot", snapshot_id, "recorded", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/capacity-snapshots")
def list_capacity(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    rows = conn.execute(select(capacity_snapshots).order_by(capacity_snapshots.c.captured_at.desc()))
    return [_row(row) or {} for row in rows]


# --- Allocation -----------------------------------------------------------


@app.post("/v1/projects/{project_id}/allocations", status_code=201)
def create_allocation(
    project_id: str,
    body: AllocationCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "tre_operator")
    _get_project(conn, project_id)
    allocation_id = str(uuid.uuid4())
    payload = {
        "id": allocation_id,
        "project_id": project_id,
        "cpu_vcpu": body.cpu_vcpu,
        "memory_gib": body.memory_gib,
        "gpu_count": body.gpu_count,
        "storage_gib": body.storage_gib,
        "status": "RESERVED",
        "approved_by": actor.username,
        "rationale": body.rationale,
    }
    conn.execute(allocations.insert().values(**payload))
    record_event(conn, "allocation", allocation_id, "reserved", actor, after=payload)
    conn.commit()
    return payload


@app.get("/v1/allocations")
def list_allocations(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    rows = conn.execute(select(allocations).order_by(allocations.c.created_at))
    return [_row(row) or {} for row in rows]


# --- Data governance ------------------------------------------------------


@app.post("/v1/projects/{project_id}/data-requests", status_code=201)
def create_data_request(
    project_id: str,
    body: DataRequestCreate,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _get_project(conn, project_id)
    request_id = str(uuid.uuid4())
    payload = {
        "id": request_id,
        "project_id": project_id,
        "direction": body.direction,
        "dataset_name": body.dataset_name,
        "classification": body.classification,
        "owner": body.owner,
        "criteria_json": _jsonable(body.criteria),
        "rationale": body.rationale,
        "status": "PENDING",
        "decided_by": None,
        "decision_rationale": "",
    }
    conn.execute(data_requests.insert().values(**payload))
    record_event(conn, "data_request", request_id, "submitted", actor, after=payload)
    conn.commit()
    return payload


@app.post("/v1/data-requests/{request_id}/decision")
def decide_data_request(
    request_id: str,
    body: DataDecision,
    conn: Connection = Depends(get_conn),
    actor: Actor = Depends(require_actor),
) -> dict[str, Any]:
    _require_roles(actor, "dpo", "data_owner")
    request_row = conn.execute(
        select(data_requests).where(data_requests.c.id == request_id)
    ).first()
    if not request_row:
        raise HTTPException(status_code=404, detail="Data request not found")
    status = "APPROVED" if body.decision == "approved" else "REJECTED"
    conn.execute(
        update(data_requests)
        .where(data_requests.c.id == request_id)
        .values(
            status=status,
            decided_by=body.decided_by,
            decision_rationale=body.rationale,
            criteria_json=_jsonable(body.criteria),
        )
    )
    record_event(
        conn,
        "data_request",
        request_id,
        body.decision,
        actor,
        before={"status": request_row.status},
        after={"status": status},
        reason=body.rationale,
    )
    conn.commit()
    return {"id": request_id, "status": status}


@app.get("/v1/projects/{project_id}/data-requests")
def list_data_requests(project_id: str, conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    _get_project(conn, project_id)
    rows = conn.execute(
        select(data_requests)
        .where(data_requests.c.project_id == project_id)
        .order_by(data_requests.c.created_at)
    )
    return [_row(row) or {} for row in rows]


@app.get("/v1/assessments")
def list_all_assessments(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return [
        _row(row) or {}
        for row in conn.execute(select(assessments).order_by(assessments.c.created_at.desc()))
    ]


@app.get("/v1/quotes")
def list_all_quotes(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return [_row(row) or {} for row in conn.execute(select(quotes).order_by(quotes.c.created_at.desc()))]


@app.get("/v1/ledger-entries")
def list_all_ledger_entries(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return [
        _row(row) or {}
        for row in conn.execute(select(ledger_entries).order_by(ledger_entries.c.created_at.desc()))
    ]


@app.get("/v1/data-requests")
def list_all_data_requests(conn: Connection = Depends(get_conn)) -> list[dict[str, Any]]:
    return [
        _row(row) or {}
        for row in conn.execute(select(data_requests).order_by(data_requests.c.created_at.desc()))
    ]


@app.get("/v1/outbox")
def list_outbox(
    status: str | None = Query(default="PENDING"), conn: Connection = Depends(get_conn)
) -> list[dict[str, Any]]:
    query = select(outbox_events)
    if status:
        query = query.where(outbox_events.c.status == status)
    return [_row(row) or {} for row in conn.execute(query.order_by(outbox_events.c.created_at))]
