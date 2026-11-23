# Milestone 1 Closure

Status: CLOSED  
Closed: 2026-11-23  
Scope: Platform foundation (blueprint §9, Stage 1)

---

## What was built

| File | Responsibility |
|---|---|
| `backend/pyproject.toml` | Pinned dependencies, ruff/mypy/pytest/import-linter config |
| `backend/.env.example` | Dev template (runtime role + owner role URLs, JWT, LLM placeholder) |
| `.gitignore` + `git init` | Repo hygiene, secrets excluded |
| `backend/app/core/config.py` | Typed settings, fail-fast, SecretStr, scheme validation, env-specific CORS rules |
| `backend/app/core/logging.py` | JSON + plain formatters, ContextVars, redaction filter (JWT / Bearer / URL passwords / key-name scrub) |
| `backend/app/core/db.py` | Two engines (runtime + owner), `tenant_session` (RLS GUC), `admin_session` (owner), healthcheck |
| `infra/docker-compose.yml` | Postgres 16 + pgvector, healthcheck, init scripts |
| `infra/init/01-extensions.sql` | pgvector, pgcrypto, citext |
| `infra/init/02-roles.sql` | `auralink_app` runtime role, default grants |
| `backend/app/api/deps.py` | Shared FastAPI dependencies |
| `backend/app/api/v1/health.py` | `/healthz`, `/__meta`, `/metrics` stub |
| `backend/app/main.py` | App factory, lifespan, request-ID + trace-ID middleware, CORS, TrustedHost, exception handler |
| `backend/run.py` | Windows-safe entrypoint with selector event loop |

---

## Verified behaviors

| Behavior | Test | Result |
|---|---|---|
| RLS blocks cross-tenant reads | 3-org test against live Postgres | `org_a tries B: []` |
| Two-role separation | `db.py` uses `auralink_app` for runtime, `auralink` for admin | confirmed via engine URL inspection |
| Secret masking | `SecretStr` rendered as `**********` in logs and repr | confirmed |
| Log redaction | Bearer token, API key, URL password, nested dict all scrubbed | five-log test passed |
| Context propagation | `trace_id`/`org_id`/`user_id`/`request_id` in every line while bound | confirmed |
| Context clearing | No context fields after `clear_context()` | confirmed |
| Fail-fast config | Missing `DATABASE_URL` or `JWT_SECRET` -> `ValidationError` at boot | confirmed |
| Wrong scheme rejection | `postgresql://` (sync) rejected with clear message | confirmed |
| HTTP server | `TcpTestSucceeded: True`, five endpoints return expected payloads | confirmed |
| Request ID echo | Caller-supplied `X-Request-ID` echoed on response | confirmed |
| Health endpoint | `{"status":"ok","db":"ok"}` with live Postgres | confirmed |

---

## Real defects caught by gating, not by inspection

### 1. RLS owner bypass
First RLS test printed `org_a sees: ['A', 'B']` because the app user created the table (owner bypasses RLS).  
**Fix:** separate owner role (`auralink`) and runtime role (`auralink_app`); owner tables use `ALTER TABLE ... FORCE ROW LEVEL SECURITY`.  
**Gate:** no tenant table ships without `FORCE ROW LEVEL SECURITY` and a policy based on `current_setting('app.org_id')`.

### 2. Host Postgres shadowing the container
`Get-NetTCPConnection` showed the host `postgresql-x64-18` service owning `0.0.0.0:5432` while Docker owned `::5432`.  
**Fix:** stop and disable the host service; container owns 5432 exclusively.  
**Gate:** verify `Get-NetTCPConnection -LocalPort 5432` shows only Docker-owned entries after any machine setup.

### 3. Windows Proactor event loop never accepts connections
uvicorn printed "Uvicorn running on..." but `TcpTestSucceeded` was False.  
**Fix:** `run.py` uses `loop="asyncio:SelectorEventLoop"` on Windows; uvicorn pinned to 0.54.0.  
**Gate:** any endpoint test failing with `Unable to connect` while uvicorn reports running -> check `Test-NetConnection` first, then confirm uvicorn version and loop factory.

---

## Permanent gates for Milestone 2+

1. **RLS gate.** Every tenant table requires `FORCE ROW LEVEL SECURITY` and a policy using `current_setting('app.org_id')`. A CI test enumerates tenant tables and fails if any lacks RLS. Behavioral test against live Postgres proves cross-tenant read returns empty.
2. **Secrets gate.** All secret fields use `SecretStr`. Redaction filter is tested at every new logger consumer. No f-string of a secret anywhere.
3. **Write-verify gate.** Any file written by PowerShell must verify with `Test-Path`, `(Get-Item).Length`, `compileall`, and `Format-Hex` (no BOM) before the next file.
4. **Behavioral-test gate.** Every module closes only after a live test proves the behavior it claims — not after inspection.
5. **Venv gate.** `pip` and `python` commands run only when the prompt shows `(.venv)`. `run.py` exists for Windows uvicorn; direct `uvicorn` CLI is deprecated for this repo on Windows.

---

## Test coverage carried forward

The live tests in this milestone were run interactively. Before Milestone 2 closes, they must be extracted to real test files:

- `backend/tests/tenant_isolation/test_rls.py` — cross-tenant read returns empty
- `backend/tests/security/test_redaction.py` — five redaction cases + SecretStr masking
- `backend/tests/api/test_health.py` — health, meta, metrics, header echo
- `backend/tests/config/test_settings.py` — fail-fast on missing vars, scheme rejection, CORS env rules
- `backend/tests/conftest.py` — fixtures for ephemeral DB (transaction-rollback per test), settings override via `get_settings.cache_clear()`

Deferred to Milestone 2: these test files plus a CI workflow that runs `ruff`, `mypy`, `compileall`, `import-linter`, `pytest`.

---

## ADR list (accepted)

| ADR | Decision |
|---|---|
| ADR-0001 | MVP wedge: inbound inquiry triage |
| ADR-0002 | Tenant isolation: shared schema + RLS + app scoping |
| ADR-0003 | LLM provider: one primary behind adapter (provider TBD in 0003a) |
| ADR-0004 | Vector search: pgvector |
| ADR-0005 | Job queue: Postgres outbox + SKIP LOCKED |
| ADR-0006 | Agent orchestration: custom state machine |
| ADR-0007 | Workflow engine: custom native |
| ADR-0008 | Auth: first-party email/password + JWT |
| ADR-0008a | PyJWT over python-jose |
| ADR-0009 | First channel: first-party website chat |
| ADR-0010 | Billing: deferred |
| ADR-0011 | Retention defaults (90d executions, 180d messages, 1y audit) |
| ADR-0012 | Python 3.12 pinned in containers (local dev is 3.12.10 now) |
| ADR-0013 | Observability: logs + DB-stored LLM traces, no external APM |
| ADR-0014 | Hosting: single managed VM + managed Postgres (later) |
| ADR-0015 | Architecture: modular monolith with import-linter enforcement |
| ADR-0016 | Windows event loop: `loop="asyncio:SelectorEventLoop"` in `run.py`; uvicorn pinned to 0.54.0 |
| ADR-0017 | Two DB roles: owner (`auralink`) for migrations/admin_session, runtime (`auralink_app`) for tenant_session |

---

## Deferred to Milestone 2

- Extract live tests to `backend/tests/`.
- Alembic bootstrap (`alembic.ini`, `migrations/env.py` wired to `ALEMBIC_DATABASE_URL`).
- CI workflow (`.github/workflows/ci.yml`) running ruff + mypy + compileall + import-linter + pytest.
- Frontend shell (login, layout, navigation) — currently only `npm create vite` scaffold.
- `README.md` quickstart (setup, run, test, stop).

---

## Milestone 2 entry

Milestone 2 (Agent engine) starts with:

1. `app/adapters/llm/base.py` — provider interface (`LLMRequest`, `LLMResponse`, `ToolCall`, `TokenUsage`)
2. `app/adapters/llm/openai_compat.py` — OpenAI-compatible adapter (streaming later)
3. `app/modules/agents/models.py` + `schemas.py` + `service.py` — agent definition, versioning, service
4. Alembic baseline migration
5. First working agent test console

Same gates apply. One file at a time. Verify before proceeding.
