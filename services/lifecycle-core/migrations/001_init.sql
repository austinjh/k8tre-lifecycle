-- Initial lifecycle-core schema for CloudNativePG.
-- The FastAPI POC creates the same tables with SQLAlchemy metadata.create_all();
-- this migration is the production-shaped schema used by the deployed database.

CREATE TABLE IF NOT EXISTS studies (
    id           text PRIMARY KEY,
    title        varchar(300) NOT NULL,
    description  text NOT NULL DEFAULT '',
    owner        varchar(120) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projects (
    id           text PRIMARY KEY,
    study_id     text NOT NULL REFERENCES studies(id),
    name         varchar(120) NOT NULL UNIQUE,
    description  text NOT NULL DEFAULT '',
    state        varchar(40) NOT NULL DEFAULT 'DRAFT',
    control_tier varchar(40),
    owner        varchar(120) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS people (
    id          text PRIMARY KEY,
    issuer      varchar(300) NOT NULL,
    subject     varchar(300) NOT NULL,
    username    varchar(120) NOT NULL,
    email       varchar(200) NOT NULL DEFAULT '',
    roles_json  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (issuer, subject)
);

CREATE TABLE IF NOT EXISTS datasets (
    id             text PRIMARY KEY,
    project_id     text NOT NULL REFERENCES projects(id),
    name           varchar(200) NOT NULL,
    source         text NOT NULL DEFAULT '',
    classification varchar(40) NOT NULL,
    owner          varchar(120) NOT NULL,
    retention_until varchar(40),
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence (
    id          text PRIMARY KEY,
    project_id  text NOT NULL REFERENCES projects(id),
    kind        varchar(60) NOT NULL,
    object_key  varchar(500) NOT NULL,
    sha256      varchar(64),
    created_by  varchar(120) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approvals (
    id          text PRIMARY KEY,
    project_id  text NOT NULL REFERENCES projects(id),
    kind        varchar(60) NOT NULL,
    decision    varchar(20) NOT NULL,
    decided_by  varchar(120) NOT NULL,
    rationale   text NOT NULL DEFAULT '',
    expires_at  varchar(40),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assessments (
    id             text PRIMARY KEY,
    project_id     text NOT NULL REFERENCES projects(id),
    version        varchar(20) NOT NULL,
    answers_json          jsonb NOT NULL,
    mitigations_json      jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence_required_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    risk_score            integer NOT NULL,
    classification varchar(40) NOT NULL,
    control_tier   varchar(40) NOT NULL,
    status         varchar(20) NOT NULL DEFAULT 'COMPLETE',
    created_by     varchar(120) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rate_cards (
    version        varchar(20) PRIMARY KEY,
    rates_json     jsonb NOT NULL,
    effective_from varchar(40) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quotes (
    id                text PRIMARY KEY,
    project_id        text NOT NULL REFERENCES projects(id),
    rate_card_version varchar(20) NOT NULL,
    estimate_json     jsonb NOT NULL,
    total_amount      numeric(14, 2) NOT NULL,
    currency          varchar(3) NOT NULL DEFAULT 'GBP',
    valid_until       varchar(40) NOT NULL,
    status            varchar(20) NOT NULL DEFAULT 'DRAFT',
    created_by        varchar(120) NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS budgets (
    id               text PRIMARY KEY,
    project_id       text NOT NULL REFERENCES projects(id),
    quote_id         text NOT NULL REFERENCES quotes(id),
    committed_amount numeric(14, 2) NOT NULL,
    currency         varchar(3) NOT NULL DEFAULT 'GBP',
    status           varchar(20) NOT NULL DEFAULT 'COMMITTED',
    created_by       varchar(120) NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id           text PRIMARY KEY,
    project_id   text NOT NULL REFERENCES projects(id),
    budget_id    text REFERENCES budgets(id),
    entry_type   varchar(40) NOT NULL,
    amount       numeric(14, 2) NOT NULL,
    currency     varchar(3) NOT NULL DEFAULT 'GBP',
    reference    varchar(300) NOT NULL,
    period_start varchar(40),
    period_end   varchar(40),
    resource_json jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usage_records (
    id                       text PRIMARY KEY,
    project_id               text NOT NULL REFERENCES projects(id),
    period_start             varchar(40) NOT NULL,
    period_end               varchar(40) NOT NULL,
    cpu_request_vcpu_hours   numeric(14, 2) NOT NULL,
    memory_request_gib_hours numeric(14, 2) NOT NULL,
    gpu_hours                numeric(14, 2) NOT NULL DEFAULT 0,
    node_capacity_json       jsonb NOT NULL,
    rate_card_version        varchar(20) NOT NULL,
    amount                   numeric(14, 2) NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS capacity_snapshots (
    id                  text PRIMARY KEY,
    node_name           varchar(200) NOT NULL,
    cpu_capacity_vcpu   numeric(10, 2) NOT NULL,
    memory_capacity_gib numeric(10, 2) NOT NULL,
    gpu_count           integer NOT NULL DEFAULT 0,
    captured_at         varchar(40) NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id          text PRIMARY KEY,
    project_id  text NOT NULL REFERENCES projects(id),
    cpu_vcpu    numeric(10, 2) NOT NULL,
    memory_gib  numeric(10, 2) NOT NULL,
    gpu_count   integer NOT NULL DEFAULT 0,
    storage_gib numeric(10, 2) NOT NULL DEFAULT 0,
    status      varchar(20) NOT NULL DEFAULT 'RESERVED',
    approved_by varchar(120) NOT NULL,
    rationale   text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_requests (
    id                 text PRIMARY KEY,
    project_id         text NOT NULL REFERENCES projects(id),
    direction          varchar(10) NOT NULL,
    dataset_name       varchar(200) NOT NULL,
    classification     varchar(40) NOT NULL,
    owner              varchar(120) NOT NULL,
    criteria_json      jsonb NOT NULL DEFAULT '[]'::jsonb,
    rationale          text NOT NULL DEFAULT '',
    status             varchar(20) NOT NULL DEFAULT 'PENDING',
    decided_by         varchar(120),
    decision_rationale text NOT NULL DEFAULT '',
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id               text PRIMARY KEY,
    aggregate_type   varchar(40) NOT NULL,
    aggregate_id     text NOT NULL,
    action           varchar(60) NOT NULL,
    actor            varchar(120) NOT NULL,
    actor_roles_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    before_json      jsonb,
    after_json       jsonb,
    reason           text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outbox_events (
    id           text PRIMARY KEY,
    topic        varchar(120) NOT NULL,
    payload_json jsonb NOT NULL,
    status       varchar(20) NOT NULL DEFAULT 'PENDING',
    created_at   timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);

CREATE INDEX IF NOT EXISTS ix_projects_state ON projects (state);
CREATE INDEX IF NOT EXISTS ix_evidence_project ON evidence (project_id, kind);
CREATE INDEX IF NOT EXISTS ix_approvals_project ON approvals (project_id, kind, decision);
CREATE INDEX IF NOT EXISTS ix_assessments_project ON assessments (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_quotes_project ON quotes (project_id, status);
CREATE INDEX IF NOT EXISTS ix_budgets_project ON budgets (project_id, status);
CREATE INDEX IF NOT EXISTS ix_ledger_project ON ledger_entries (project_id, entry_type, created_at);
CREATE INDEX IF NOT EXISTS ix_usage_project ON usage_records (project_id, period_start);
CREATE INDEX IF NOT EXISTS ix_allocations_project ON allocations (project_id, status);
CREATE INDEX IF NOT EXISTS ix_data_requests_project ON data_requests (project_id, status);
CREATE INDEX IF NOT EXISTS ix_audit_aggregate ON audit_events (aggregate_type, aggregate_id, created_at);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox_events (status, created_at);
