# GnosisLync

Svarm Banking Intelligence Platform: generic banking knowledge, pluggable core-banking product knowledge and
automatically discovered site knowledge, behind shared skills and APIs for RIS Sense, EKWORLD ekSense and
future Svarm products.

- **Design specification:** [`docs/README.md`](docs/README.md)
- **Status:** Phase 0 (engineering foundations). See `docs/architecture/11-implementation-plan.md`.

## What exists today (Phase 0)

| Area | Where |
|---|---|
| Knowledge data model v1: topology, artefacts/evidence, entities/relations with versions, review, packages, consumer artefacts, discovery, audit | `db/sql/0001_core.sql`, `db/migrations/` |
| Tenant isolation: row-level security on every table; app role without `BYPASSRLS`; no context → zero rows | `db/migrations/versions/0001_core.py`, `gnosis/context/` |
| Lifecycle rules (candidates never auto-promote, four-eyes on HIGH significance), enforced in Python and backed by a DB trigger | `gnosis/knowledge/lifecycle.py` |
| Append-only, hash-chained audit log with verifier | `audit_event`, `gnosis_verify_audit_chain()` |
| Minimal API: `/healthz`, `GET /v1/entities/{id}`; tenant comes from the verified token, not the header | `services/gnosis_api/` |
| Quality gates: ruff, mypy --strict, import-linter boundaries, gitleaks, pytest + coverage gates, pip-audit | `.pre-commit-config.yaml`, `.github/workflows/ci.yml` |

## Local development

Requirements: [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync                                   # Python 3.12 + dependencies
uv run pre-commit install && uv run pre-commit install --hook-type pre-push

# tests: unit tests need nothing; DB tests start pgvector/pgvector:pg16 via Testcontainers
uv run pytest -m unit
uv run pytest                              # everything
uv run pytest --cov --cov-report=json && uv run python scripts/check_coverage.py

# optional: run the API against a local database (see "Hands-on demo" below)
docker compose -f deploy/dev/docker-compose.yml up -d postgres
GNOSIS_MIGRATION_DATABASE_URL=postgresql+psycopg://gnosis_owner:dev-only-owner@localhost:55432/gnosis \
  uv run alembic upgrade head
GNOSIS_JWT_HS256_SECRET=$(openssl rand -hex 32) uv run uvicorn gnosis_api.main:app --reload
```

To run DB tests against an existing empty database instead of Testcontainers, set
`GNOSIS_TEST_DATABASE_URL` to a superuser/owner URL.

### Hands-on demo (Phase 0)

Dev stack ports are localhost-only and offset to avoid clashes: Postgres `55432`, MinIO `59000/59001`,
Vault `58200`. Demo data: two banks (`DEMO_A`, `DEMO_B`) each with their own meaning of the same T24
field, one AI-inferred HIGH-significance candidate rule in Bank A, and one shared verified concept.

```bash
docker compose -f deploy/dev/docker-compose.yml up -d postgres
GNOSIS_MIGRATION_DATABASE_URL=postgresql+psycopg://gnosis_owner:dev-only-owner@localhost:55432/gnosis \
  uv run alembic upgrade head
uv run python scripts/demo.py seed        # idempotent; prints tenant and entity ids
uv run python scripts/demo.py guards      # tries to break isolation/lifecycle/audit rules; all must be BLOCKED

# terminal 1
export GNOSIS_JWT_HS256_SECRET=$(openssl rand -hex 32)
uv run uvicorn gnosis_api.main:app --port 58080

# terminal 2 (export the same GNOSIS_JWT_HS256_SECRET)
A=$(uv run python scripts/demo.py token --tenant DEMO_A)
AB=$(uv run python scripts/demo.py token --tenant DEMO_A --tenant DEMO_B --sub user:consultant)
U=http://127.0.0.1:58080/v1/entities
H="X-Gnosis-Purpose: REPORTING"
curl -s -H "Authorization: Bearer $A" -H "$H" $U/3c1b6ff4-0a49-54f2-a57f-608e92b91e7c   # 200 own field
curl -s -H "Authorization: Bearer $A" -H "$H" $U/81cb12f9-f533-5c53-9941-530b981ee417   # 404 Bank B's field
curl -s -H "Authorization: Bearer $A" -H "$H" $U/15edbe4c-8595-5293-8dd1-d058fb8d7df8   # 200 shared concept
curl -s -H "Authorization: Bearer $A" -H "$H" -H "X-Gnosis-Tenant: DEMO_B" \
  $U/81cb12f9-f533-5c53-9941-530b981ee417                                               # 403 not entitled
curl -s -H "Authorization: Bearer $AB" -H "$H" -H "X-Gnosis-Tenant: DEMO_B" \
  $U/81cb12f9-f533-5c53-9941-530b981ee417                                               # 200 consultant
```

Interactive API docs: http://127.0.0.1:58080/docs. Reset everything:
`docker compose -f deploy/dev/docker-compose.yml down -v`.

### Database roles

| Role | Purpose |
|---|---|
| schema owner (`gnosis_owner` in dev) | runs migrations; owns all objects |
| `gnosis_app` (NOLOGIN) | privileges for the application: SELECT/INSERT/UPDATE, no DELETE, no BYPASSRLS |
| `gnosis_api` (LOGIN, member of `gnosis_app`) | what the API connects as |

Migrations are forward-only (expand/contract); `downgrade` is intentionally not implemented.

## Repository settings to apply on GitHub (not configurable from the repo)

Protect `main`:
- require a pull request with code-owner review (2 approvals for `db/`, lifecycle, context, auth, `.github/`),
- dismiss stale approvals; require linear history and signed commits; block force-pushes,
- required status checks: `static`, `secrets`, `tests`, `dependencies`.

Local hooks are a convenience; CI with branch protection is the enforced gate.
