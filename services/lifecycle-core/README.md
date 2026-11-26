# lifecycle-core

API-first service for the K8TRE research lifecycle prototype. It owns project
state, governance evidence, risk assessments, quotes/budgets/ledger entries,
capacity/usage records, allocations and data-transfer requests.

The browser never calls this service. The forked `k8tre-backend` portal renders
Jinja pages from its own Keycloak session and calls lifecycle-core server-side
with trusted actor headers. `LIFECYCLE_INTERNAL_TOKEN` prevents unrelated
in-cluster workloads from calling the API.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
LIFECYCLE_DATABASE_URL=sqlite:///:memory: uvicorn app.main:app --reload
pytest -q
```

OpenAPI is served at `/docs`; the health check is `/healthz`.

## State machine

`DRAFT → SUBMITTED → ASSESSING → GOVERNANCE_REVIEW → COSTED →
BUDGET_APPROVED → PROVISIONING → ACTIVE → SUSPENDED/CLOSED → ARCHIVED`.

Each transition is role-gated and checks prerequisite evidence. Every mutation
writes an append-only `audit_events` row and a transactional `outbox_events`
row. No event is published directly from a request handler.

## Workers

- `python -m app.workers.outbox` marks pending outbox rows processed. Its
  publisher is a no-op log hook in the POC.
- `python -m app.workers.collector --hours 1 --post http://lifecycle-core:8000`
  reads Kubernetes requests and node allocatable capacity, applies the rate
  card, and records simulated usage. It does not move data or change network
  policy.

## Database

POC runs use `sqlite:///:memory:` by default. Deployed instances use
CloudNativePG PostgreSQL via `LIFECYCLE_DATABASE_URL`. `migrations/001_init.sql`
is the production-shaped schema; SQLAlchemy `create_all` keeps tests and local
runs self-contained.
