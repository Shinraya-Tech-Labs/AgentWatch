# Packaging and dependency integrity

## What ships

| Artifact | Contents |
|---|---|
| wheel `panoptis-<v>-py3-none-any.whl` | the `panoptis` and `panopti` packages, including `panoptis/evidence/verifier.py` and `panoptis/protect/claude_hook_entry.py`; console scripts `panoptis` and `panopti` |
| sdist `panoptis-<v>.tar.gz` | the two packages, their tests, `scripts/check_imports.py`, `pyproject.toml`, README, LICENSE, CHANGELOG. **Not** the dashboard, the landing site (a separate repository), deployment files or design notes. Before TO-030 it shipped all of those, including the landing site's `.next` build output (7 MB). |

Python: `>=3.12,<3.13` (CI target 3.12). The test suite also passes on 3.14, but that is not yet a supported claim.

## Dependencies

- **Every third-party import is declared.** `python scripts/check_imports.py` checks every import of the shipped packages. A module-level import must be a core dependency; a guarded import (in a function or `try/except ImportError`) may come from an extra. Found and fixed:
  - `starlette`, imported directly but undeclared → core;
  - `protobuf` → `otel` extra;
  - `reportlab` → new `reports` extra;
  - `groq` → new `groq` extra;
  - the obsolete `typer[all]` extra, which no longer exists, → `typer`.
- **Extras:**
  - `embeddings`, `otel`, `crypto`, `redaction`, `langchain`, `docs`, `postgres`;
  - `reports`, `groq`, `discovery` (`psutil`);
  - `router` (`litellm`, for `panoptis.infrastructure.router`): a core dependency up to 0.4.2. Its pins conflicted with tools installed next to PANOPTIS (python-dotenv with fastmcp), and nothing else imports it;
  - `dev`; and `all`, which combines `embeddings`, `otel`, `crypto`, `langchain`, `postgres` and `discovery`.
- **Security floors** (`pyproject.toml`):
  - `fastapi>=0.162` / `starlette>=1.3.1`: Starlette 0.52.1 had PYSEC-2026-161, -248, -249, -2280 and -2281. All API, protect and v3 tests (1265) pass on FastAPI 0.162 / Starlette 1.10.
  - `filelock>=4.2.1`, a transitive dependency of huggingface-hub and litellm: GHSA-v4x8-3wr5-3wq8 and 12 related advisories in 3.32.7.
  - `mcp>=1.8` (Streamable HTTP).
- **Reproducible set:** `constraints/py312.txt` is the frozen, audited result of a clean install from the sdist. Install with `pip install -c constraints/py312.txt panoptis`.

## Verifying a build

```bash
scripts/clean_install.sh python3.12
```

This builds both artifacts in a fresh build environment and installs each into its own new virtualenv. It runs `scripts/smoke_installed.py` from outside the repository with `python -I`, so the installed files are tested, not the checkout. The smoke test checks that:

- both packages import;
- both console scripts run;
- a protected tool runs;
- a dangerous call is blocked before it runs;
- the evidence verifies;
- the standalone verifier is present.

The script then runs `pip-audit` on the installed set and fails on any known vulnerability.

Last run: both artifacts installed and passed the smoke test, and `pip-audit` found **no known vulnerabilities**. The only skip is `panoptis` itself, which is not on PyPI under this version.

## Dashboard (`frontend/`)

- **Next.js 14.2.30 → 16.4.4.** With React 18.3, the Pages Router is unchanged. Next 14.2.35 (the last 14.x) still had advisories fixed only in 15.5.10+/16.x: DoS, request smuggling in rewrites, image-optimizer issues, and the bundled PostCSS 8.4.31.
- **Lint moved to ESLint 9 with a flat config** (`eslint.config.mjs`) because `next lint` was removed in Next 16. The `react-hooks/purity` finding was fixed: the stale indicator no longer reads the clock during render.
- **`type-check` runs `next typegen` first.**
- **Audit:** `npm audit --omit=dev` reports **0** vulnerabilities in runtime dependencies. 35 low/moderate advisories remain in development-only tooling (`minimatch`, `@jest/*`, `@typescript-eslint/*`, `eslint` internals). They are not shipped in the standalone image, and their fixes require breaking major upgrades of Jest and ESLint, which is tracked for the next tooling upgrade.
- **Verified after the upgrade:** type-check, lint, 36 Jest tests, the production build and the 4 browser E2E tests against a running backend all pass.
