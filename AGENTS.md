# PANOPTIS (formerly AgentWatch) — Agent Instructions

## Repo Structure

Three independent codebases in one repo:

| Path | What | Stack |
|---|---|---|
| `panoptis/` | Python package (`panoptis`) | FastAPI, SQLAlchemy async, Celery, Pydantic |
| `frontend/` | Dashboard UI | Next.js 16 (pages router), React 18, Tailwind 3, Jest |
| `agentwatch-landing/` | Public landing page | Next.js 16, React 19, GSAP, Three.js |

**Critical:** `frontend/` and `agentwatch-landing/` are separate Next.js apps (pages router + React 18 vs app router + React 19, Tailwind 3 vs 4). Do not mix deps or share components between them.

## Formatting (issue #644)

- **Python** (`panoptis/`): `ruff format panoptis/` (config in `[tool.ruff.format]` of `pyproject.toml`). Double quotes, docstring-code-format on.
- **Frontend** (`frontend/`): `npm run format` (Prettier 3.4 with the shared `.prettierrc.json`).
- **Landing** (`agentwatch-landing/`): `npm run format` (Prettier 3.4).
- **All-in-one**: `make format` from the repo root runs all three.
- Verify without modifying: `make format-check`.

## Python

```bash
# Install (from repo root)
pip install -e ".[dev]"

# Lint
ruff check panoptis/
ruff format --check panoptis/

# Tests (run without services; Postgres-store and connectivity tests are skipped unless
# AGENTWATCH_TEST_PG_URL / DATABASE_URL / REDIS_URL are set — CI sets them)
pytest tests/ -v --cov=panoptis --cov-fail-under=70
```

- Python 3.12+ (supported and tested through 3.14). Build system: hatchling.
- `tests/conftest.py` mocks sentence-transformers — do not remove; tests will hang downloading models.
- Coverage gate: 70% minimum (enforced in CI).
- Ruff ignores `E501` (line length), `S101` (assert), `S104` (0.0.0.0 bind), `S202` (tarfile.extractall). These are intentional.
- `__init__.py` files use wildcard re-exports — ruff `F401`/`F403` are suppressed per-file.
- `asyncio_mode = "auto"` — no need for `@pytest.mark.asyncio` on async tests.

## Frontend (`frontend/`)

```bash
cd frontend
npm ci
npm run type-check   # tsc --noEmit
npm run build
npm run test         # Jest
```

- API URL defaults to `/api/v1`. Override with `NEXT_PUBLIC_API_URL`.
- WebSocket endpoint at `/ws/events`.
- Two builds: `npm run build` (standalone server, Docker Compose) and `npm run build:static`
  (`output: 'export'`, copied to `panoptis/_dashboard/`, shipped in the wheel and served by
  `panoptis up`). The static build leaves out `pages/api` (every `.ts` file there).

## Local mode

`panoptis up` runs the API + static dashboard on 127.0.0.1 with the SQLite store under
`AGENTWATCH_HOME` (`panoptis/local.py`, `panoptis/cli/local_cmds.py`). It never writes sample
data: `panoptis demo` uses a separate workspace (`<home>/demo`, port 8001) that
`panoptis demo --clear` deletes, and `seed_demo_workspace()` refuses to run outside it. `/api/local/session` and
the static mount exist only when `PANOPTIS_LOCAL_UI=1`; that block must stay last in
`panoptis/api/server.py`. `scripts/smoke_up.py` is the end-to-end check (CI runs it on a clean
install).

## Landing Page (`agentwatch-landing/`)

**Next.js 16 — this is NOT the version you trained on.** API conventions, file structure, and React patterns may differ. Read `node_modules/next/dist/docs/` before writing code. Heed deprecation notices.

```bash
cd agentwatch-landing
npm run dev
```

Static export (`output: 'export'`) deployed to GitHub Pages by `.github/workflows/landing.yml`
(`NEXT_PUBLIC_BASE_PATH` comes from `actions/configure-pages`). No ISR, route handlers that
read the request, or default image optimization.

## CI

- **CI** (`ci.yml`): lint → tests → integration tests → frontend build → Docker build. Tests and integration tests run with Postgres+Redis service containers.
- **PR tests** (`test-on-pr.yml`): runs pytest + ruff, but only pytest is a hard gate. Lint is reported but not blocking.
- `ci.yml` runs the unit tests and the clean-install smoke test on Python 3.12, 3.13 and 3.14; lint, types and Docker stay on 3.12. `test-on-pr.yml` uses 3.12.

## Docker

```bash
docker compose up -d   # Postgres (pgvector), Redis, API, frontend — needs AGENTWATCH_API_KEY in .env
docker compose --profile workers up -d   # + Celery worker
docker compose --profile tracing up -d   # + Jaeger UI on :16686
```

Services: `postgres` (pgvector:pg16), `redis`, `api` (FastAPI on :8000, image: `Dockerfile.api`), `worker` (Celery, image: `Dockerfile.worker`, behind `workers` profile), `frontend` (Next.js 14 on :3000, image: `frontend/Dockerfile` standalone output). Optional: `jaeger` (`tracing` profile).

Dockerfiles:

- `Dockerfile.api` — Python 3.12-slim, multi-stage pip wheel build, non-root user, healthcheck on `/health`.
- `Dockerfile.worker` — same builder/runtime pattern; its default CMD launches `celery worker` (docker-compose supplies only the broker/database environment).
- `frontend/Dockerfile` — Node 20-alpine, Next.js standalone output, non-root user, healthcheck on `/api/health`.

`.dockerignore` excludes `.git`, `node_modules`, `.next`, `.pytest_cache`, `.ruff_cache`, tests, IDE configs.

## Environment

Copy `.env.example` to `.env`. Key vars:

- `DATABASE_URL` — asyncpg connection string (overrides individual `DB_*` vars)
- `REDIS_URL` / `CELERY_BROKER_URL`
- `AGENTWATCH_API_KEY` — required in production, optional in dev
- `ANTHROPIC_API_KEY` — used by Reasoning Auditor

## Installed Skills (`.agents/skills/`)

| Skill | Use Case |
|---|---|
| `docker-expert` | Optimize Dockerfiles, harden container security, design multi-stage builds, troubleshoot compose setups |
| `python-design-patterns` | Refactor God classes, pick composition vs inheritance, reduce coupling, structure new services cleanly |
| `python-observability` | Add structured logging, wire up Prometheus metrics, set up distributed tracing, debug prod issues |
| `seo-audit` | Audit meta tags, diagnose ranking drops, fix crawl/indexing errors, review Core Web Vitals |
| `wcag-audit-patterns` | Run WCAG 2.2 accessibility audits, fix violations, implement accessible components, meet ADA/508 |

## Gotchas

- `agentwatch-landing/` already has its own `AGENTS.md` (Next.js 16 rules). Do not overwrite it.
- User-visible text says PANOPTIS; env vars (`AGENTWATCH_*`), `~/.agentwatch` and class names keep the old name on purpose (compatibility).
- `typer` is capped `<0.28` (vendored click breaks `tests/test_cli_surface.py`).
- Release: bump version in `pyproject.toml`, then `git tag vX.Y && git push origin vX.Y`. PyPI publish is automatic.
- CONTRIBUTING.md requires issue assignment before work begins. PRs on unassigned issues may be closed.
- CLI commands are routed through `panoptis/cli/_utils` — do not shell out directly.
