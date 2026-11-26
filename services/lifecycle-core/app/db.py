from __future__ import annotations

import os

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

DATABASE_URL = os.environ.get("LIFECYCLE_DATABASE_URL", "")
if not DATABASE_URL:
    if os.environ.get("LIFECYCLE_DB_HOST"):
        DATABASE_URL = (
            "postgresql+psycopg://"
            f"{os.environ['LIFECYCLE_DB_USER']}:{os.environ['LIFECYCLE_DB_PASSWORD']}"
            f"@{os.environ['LIFECYCLE_DB_HOST']}:{os.environ.get('LIFECYCLE_DB_PORT', '5432')}"
            f"/{os.environ['LIFECYCLE_DB_NAME']}"
        )
    else:
        DATABASE_URL = "sqlite:///:memory:"

metadata = MetaData()


def _created() -> Column:
    return Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False)


studies = Table(
    "studies",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(300), nullable=False),
    Column("description", Text, default=""),
    Column("owner", String(120), nullable=False),
    _created(),
)

projects = Table(
    "projects",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("study_id", String(36), ForeignKey("studies.id"), nullable=False),
    Column("name", String(120), nullable=False, unique=True),
    Column("description", Text, default=""),
    Column("state", String(40), nullable=False, default="DRAFT"),
    Column("control_tier", String(40), nullable=True),
    Column("owner", String(120), nullable=False),
    _created(),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

people = Table(
    "people",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("issuer", String(300), nullable=False),
    Column("subject", String(300), nullable=False),
    Column("username", String(120), nullable=False),
    Column("email", String(200), default=""),
    Column("roles_json", JSON, default=list),
    _created(),
)

datasets = Table(
    "datasets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("name", String(200), nullable=False),
    Column("source", Text, default=""),
    Column("classification", String(40), nullable=False),
    Column("owner", String(120), nullable=False),
    Column("retention_until", String(40), nullable=True),
    _created(),
)

evidence = Table(
    "evidence",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("kind", String(60), nullable=False),
    Column("object_key", String(500), nullable=False),
    Column("sha256", String(64), nullable=True),
    Column("created_by", String(120), nullable=False),
    _created(),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("kind", String(60), nullable=False),
    Column("decision", String(20), nullable=False),
    Column("decided_by", String(120), nullable=False),
    Column("rationale", Text, default=""),
    Column("expires_at", String(40), nullable=True),
    _created(),
)

assessments = Table(
    "assessments",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("version", String(20), nullable=False),
    Column("answers_json", JSON, nullable=False),
    Column("mitigations_json", JSON, default=list),
    Column("evidence_required_json", JSON, default=list),
    Column("risk_score", Integer, nullable=False),
    Column("classification", String(40), nullable=False),
    Column("control_tier", String(40), nullable=False),
    Column("status", String(20), nullable=False, default="COMPLETE"),
    Column("created_by", String(120), nullable=False),
    _created(),
)

rate_cards = Table(
    "rate_cards",
    metadata,
    Column("version", String(20), primary_key=True),
    Column("rates_json", JSON, nullable=False),
    Column("effective_from", String(40), nullable=False),
    _created(),
)

quotes = Table(
    "quotes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("rate_card_version", String(20), nullable=False),
    Column("estimate_json", JSON, nullable=False),
    Column("total_amount", Numeric(14, 2), nullable=False),
    Column("currency", String(3), nullable=False, default="GBP"),
    Column("valid_until", String(40), nullable=False),
    Column("status", String(20), nullable=False, default="DRAFT"),
    Column("created_by", String(120), nullable=False),
    _created(),
)

budgets = Table(
    "budgets",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("quote_id", String(36), ForeignKey("quotes.id"), nullable=False),
    Column("committed_amount", Numeric(14, 2), nullable=False),
    Column("currency", String(3), nullable=False, default="GBP"),
    Column("status", String(20), nullable=False, default="COMMITTED"),
    Column("created_by", String(120), nullable=False),
    _created(),
)

ledger_entries = Table(
    "ledger_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("budget_id", String(36), ForeignKey("budgets.id"), nullable=True),
    Column("entry_type", String(40), nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
    Column("currency", String(3), nullable=False, default="GBP"),
    Column("reference", String(300), nullable=False),
    Column("period_start", String(40), nullable=True),
    Column("period_end", String(40), nullable=True),
    Column("resource_json", JSON, nullable=True),
    _created(),
)

usage_records = Table(
    "usage_records",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("period_start", String(40), nullable=False),
    Column("period_end", String(40), nullable=False),
    Column("cpu_request_vcpu_hours", Numeric(14, 2), nullable=False),
    Column("memory_request_gib_hours", Numeric(14, 2), nullable=False),
    Column("gpu_hours", Numeric(14, 2), nullable=False, default=0),
    Column("node_capacity_json", JSON, nullable=False),
    Column("rate_card_version", String(20), nullable=False),
    Column("amount", Numeric(14, 2), nullable=False),
    _created(),
)

capacity_snapshots = Table(
    "capacity_snapshots",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("node_name", String(200), nullable=False),
    Column("cpu_capacity_vcpu", Numeric(10, 2), nullable=False),
    Column("memory_capacity_gib", Numeric(10, 2), nullable=False),
    Column("gpu_count", Integer, nullable=False, default=0),
    Column("captured_at", String(40), nullable=False),
)

allocations = Table(
    "allocations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("cpu_vcpu", Numeric(10, 2), nullable=False),
    Column("memory_gib", Numeric(10, 2), nullable=False),
    Column("gpu_count", Integer, nullable=False, default=0),
    Column("storage_gib", Numeric(10, 2), nullable=False, default=0),
    Column("status", String(20), nullable=False, default="RESERVED"),
    Column("approved_by", String(120), nullable=False),
    Column("rationale", Text, default=""),
    _created(),
)

data_requests = Table(
    "data_requests",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", String(36), ForeignKey("projects.id"), nullable=False),
    Column("direction", String(10), nullable=False),
    Column("dataset_name", String(200), nullable=False),
    Column("classification", String(40), nullable=False),
    Column("owner", String(120), nullable=False),
    Column("criteria_json", JSON, default=list),
    Column("rationale", Text, default=""),
    Column("status", String(20), nullable=False, default="PENDING"),
    Column("decided_by", String(120), nullable=True),
    Column("decision_rationale", Text, default=""),
    _created(),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("aggregate_type", String(40), nullable=False),
    Column("aggregate_id", String(36), nullable=False),
    Column("action", String(60), nullable=False),
    Column("actor", String(120), nullable=False),
    Column("actor_roles_json", JSON, default=list),
    Column("before_json", JSON, nullable=True),
    Column("after_json", JSON, nullable=True),
    Column("reason", Text, nullable=True),
    _created(),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("topic", String(120), nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String(20), nullable=False, default="PENDING"),
    _created(),
    Column("processed_at", DateTime(timezone=True), nullable=True),
)

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        kwargs = {}
        if ":memory:" in DATABASE_URL:
            kwargs["connect_args"] = {"check_same_thread": False}
            kwargs["poolclass"] = StaticPool
        elif DATABASE_URL.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(DATABASE_URL, future=True, **kwargs)
    return _engine


def init_db() -> None:
    """Create tables for the POC. Production uses migrations/001_init.sql."""
    metadata.create_all(get_engine())
