# k8tre-lifecycle

Add-on repositories and services for the K8TRE research project lifecycle prototype.
Nothing here is deployed to the frozen phase-2 baseline VM; all artefacts target
`k8tre-lifecycle-node` (`192.168.8.41`, `dev2.k8tre.internal`).

## Contents

- `services/lifecycle-core` — API-first FastAPI service with versioned JSON endpoints and OpenAPI docs. Modules cover the project registry, state machine, DPIA-style assessment, finance/ledger, allocation and data-governance requests. One CNPG PostgreSQL database stores all lifecycle state.
- `manifests/lifecycle-core` — namespace, ExternalSecrets, CNPG cluster, RBAC, Deployment, Service and hourly usage collector CronJob.
- `manifests/object-storage` — SeaweedFS overlay using `rwo-default` instead of upstream Longhorn, with S3 access restricted to project, `lifecycle` and `backend` namespaces.
- `apps/backend` — deployment overlay for the extended portal image from `austinjh/k8tre-backend`. It reuses the existing Keycloak OIDC client and session cookie; lifecycle-core is called server-side with a shared internal token that is never sent to the browser.
- `argo/` — Argo CD `Application` manifests for the add-ons and lifecycle-core.
- `ci/ci-secrets.yaml` — secret-store definitions. Values are generated on the VM and never committed.

## Deploy order

1. Generate runtime secrets on the VM from the upstream checkout:
   `uv run ci/create-ci-secrets.py --context default --config "$HOME/k8tre-lifecycle/ci/ci-secrets.yaml"`.
2. Push to `main`; GitHub Actions publishes `ghcr.io/austinjh/k8tre-lifecycle/lifecycle-core:v0.1.0`.
3. Apply `argo/lifecycle-addons.yaml` and `argo/lifecycle-core.yaml`.
4. Push the portal extension to `austinjh/k8tre-backend` so `ghcr.io/austinjh/k8tre-backend:main` is rebuilt.
5. Point the upstream `backend` ApplicationSet at this repository's `apps/backend/envs/*` path (see `k8tre/scripts/use-lifecycle-portal.sh`).

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r services/lifecycle-core/requirements.txt pytest
.venv/bin/python -m pytest -q services/lifecycle-core/tests
```
