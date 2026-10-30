# AgentWatch Engineering Audit & Open Issues Backlog

Audit updated: 2026-10-30  
Scope reviewed: Python package, frontend dashboard, landing page, documentation, Docker assets, GitHub Actions, tests, packaging metadata, runtime hypervisor, and MCP agent security.

---

## 🏛️ Architecture & Governance

### Issue 1: Three independent applications share one repository root
- **Severity:** Medium
- **Location:** `agentwatch/`, `frontend/`, `agentwatch-landing/`
- **Description:** The repository contains a Python package, a Next.js 14 dashboard, and a Next.js 16 landing page with independent dependency graphs and runtime conventions.
- **Why it matters:** Contributors can accidentally mix dependencies, Dockerfiles, or components across apps, especially because both frontend apps use similar framework names but incompatible major versions.
- **Recommended fix:** Keep project-local config and Docker assets inside each app, document boundaries in onboarding docs, and avoid shared frontend source unless a deliberate package boundary is introduced.

### Issue 2: Public package exports depend on wildcard re-exports
- **Severity:** Low
- **Location:** `agentwatch/**/__init__.py`
- **Description:** Package entrypoints intentionally use wildcard re-exports.
- **Why it matters:** Wildcard exports make public API ownership harder to audit and can obscure unused imports.
- **Recommended fix:** Keep current behavior for compatibility, but define explicit `__all__` lists over time for stable public modules.

---

## ⚙️ Technical Debt & Refactoring

### Issue 3: Large CLI module has too many responsibilities
- **Severity:** Medium
- **Location:** `agentwatch/cli/main.py`
- **Description:** The CLI contains command routing, rendering, API calls, upgrade/checkout logic, policy display, server startup, export, compare, and live watch behavior in a single large module.
- **Why it matters:** Large command modules are hard to test in isolation and make small CLI changes risky.
- **Recommended fix:** Split by command family behind the existing Typer command surface without changing CLI behavior.

### Issue 4: Mixed getting-started filename styles
- **Severity:** Low
- **Location:** `docs/getting-started.md`, `docs/getting_started_extended.md`, `docs/cli/getting-started.md`
- **Description:** Documentation filenames mix kebab-case and snake_case.
- **Why it matters:** Inconsistent naming makes docs harder to discover and link.
- **Recommended fix:** Standardize on kebab-case for documentation paths in a compatibility-preserving pass with redirects or link updates.

### Issue 5: API response conventions are not centrally documented
- **Severity:** Low
- **Location:** `agentwatch/api/server.py`, `docs/API-reference.md`
- **Description:** API routes exist across safety, sessions, policies, telemetry, compliance, and security, but response envelope conventions are not documented in one place.
- **Why it matters:** Contributors may add inconsistent response shapes.
- **Recommended fix:** Add a contributor-facing API style section documenting existing conventions before accepting new API work.

### Issue 6: README and package metadata version references must stay synchronized
- **Severity:** Low
- **Location:** `README.md`, `pyproject.toml`, `agentwatch/cli/demo.py`
- **Description:** Version and Python support references are distributed across multiple files.
- **Why it matters:** Drift between package metadata, badges, and CLI output undermines release trust.
- **Recommended fix:** Keep version bumps atomic and maintain a release checklist that enumerates all version-bearing files.

---

## 🧪 Testing & Validation Environments

### Issue 7: Full test suite is sensitive to local Windows temp permissions
- **Severity:** Medium
- **Location:** `tests/`, local temp path `AppData\Local\Temp`
- **Description:** Tests using pytest temp fixtures can fail on Windows if pytest encounters restricted access to the temp root.
- **Why it matters:** Contributors on Windows can see unrelated fixture setup errors even when code is correct.
- **Recommended fix:** Configure a repo-local or explicit temp directory for Windows test runs and document this in developer setup.

### Issue 8: CI PR workflow intentionally does not hard-fail lint
- **Severity:** Low
- **Location:** `.github/workflows/test-on-pr.yml`
- **Description:** Ruff runs with `continue-on-error: true`; pytest is the only hard gate.
- **Why it matters:** Contributors may assume lint failures block merges or introduce unformatted code.
- **Recommended fix:** Keep documented behavior if intentional, or promote lint to a blocking check.

---

## 🔒 Security & Hardening

### Issue 9: Security baseline is large and requires periodic review
- **Severity:** Medium
- **Location:** `bandit-baseline.json`, `.github/workflows/bandit.yml`
- **Description:** Bandit runs against a large committed baseline.
- **Why it matters:** Baselines can mask real regressions if not reviewed and refreshed deliberately.
- **Recommended fix:** Add a scheduled baseline review process and require justification for any new baseline entries.

---

## 📦 Packaging & Dependencies

### Issue 10: `typer[all]` extra is obsolete with current Typer
- **Severity:** Medium
- **Location:** `pyproject.toml:35`
- **Description:** Installing the project triggers warnings because Typer no longer provides the `all` extra.
- **Why it matters:** Obsolete extras create install warnings and reduce trust in packaging metadata.
- **Recommended fix:** Replace `typer[all]` with explicit dependencies needed by the CLI (`typer`, `rich`, `shellingham`).

### Issue 11: Docs install caused dependency resolver conflict
- **Severity:** Medium
- **Location:** `pyproject.toml`
- **Description:** Installing `.[docs]` pulled `python-dotenv==1.0.1` through `litellm`, conflicting with packages requiring `python-dotenv>=1.1.0`.
- **Why it matters:** Broad unpinned dependencies make editable installs fragile in shared developer environments.
- **Recommended fix:** Pin and validate package extras in isolated Python 3.12 environments with upper/lower bounds.

### Issue 12: Dashboard dependencies include deprecated or vulnerable packages
- **Severity:** High
- **Location:** `frontend/package.json`, `frontend/package-lock.json`
- **Description:** `npm ci` reports deprecated packages and security advisories for `next@14.2.30`.
- **Why it matters:** Frontend dependency drift can introduce security vulnerabilities in the dashboard.
- **Recommended fix:** Run a targeted dependency update pass within the Next.js 14 LTS range.

### Issue 13: Landing dependency installation unvalidated in CI
- **Severity:** Medium
- **Location:** `agentwatch-landing/package.json`
- **Description:** Landing page builds on Next.js 16 need regular lockfile validation to catch upstream breaking changes.
- **Why it matters:** Next.js 16 Canary/RC changes can break animations or SSR unpredictably.
- **Recommended fix:** Add automated validation of `npm ci && npm run build` inside `agentwatch-landing/` in CI.

---

## ⚡ Newly Researched Agent Security & Runtime Issues (via Agent Reach)

### Issue 14: Model Context Protocol (MCP) Pre-Execution Tool Parameter Invariant Gate
- **Severity:** High
- **Location:** `agentwatch/runtime/`, `panopti/guard.py`
- **Description:** In multi-agent systems integrating with MCP (Model Context Protocol) servers, indirect prompt injections (IPI) can inject malicious payloads into JSON tool-call parameters (e.g. destructive SQL, path traversals, out-of-band network exfiltration). Standard text guardrails fail because the tool arguments are syntactically valid JSON.
- **Why it matters:** AI agents act as untrusted executors. Without pre-execution schema & invariant enforcement at the MCP tap, rogue or manipulated agents can execute catastrophic side-effects.
- **Recommended fix:** Implement a declarative parameter invariant evaluator in `panopti/guard.py` that intercepts MCP tool requests before execution and validates payload arguments against pre-defined safety bounds (fail-closed).

### Issue 15: Ephemeral Credential Substitution & Secret Shield for Autonomous Tool Calls
- **Severity:** High
- **Location:** `agentwatch/security/`, `agentwatch/runtime/`
- **Description:** Autonomous agents frequently hold long-lived API keys or credentials directly in tool contexts or memory. If an agent is coerced via prompt injection or its execution trace is intercepted, credentials are leaked.
- **Why it matters:** Direct credential exposure breaks the principle of least privilege in autonomous pipelines.
- **Recommended fix:** Introduce an ephemeral credential proxy in `agentwatch/security/` where the runtime substitutes raw API secrets with short-lived, scope-limited tokens at execution boundaries, ensuring the agent model never possesses raw production credentials.

### Issue 16: Stateful Execution Trace & Multi-Step Behavioral Drift Sentinel
- **Severity:** Medium
- **Location:** `agentwatch/reasoning/`, `agentwatch/telemetry/`
- **Description:** Single-turn guardrails evaluate tool calls in isolation, where individual actions (e.g. `list_dir`, `read_file`, `http_post`) appear harmless. Multi-step attacks (data harvesting followed by staging and exfiltration) are only visible across the temporal trajectory.
- **Why it matters:** Point-in-time checks have a blind spot for chained multi-turn exploitation and autonomous loop runaway.
- **Recommended fix:** Implement a sliding-window execution trace auditor that tracks intent divergence, tool execution frequency, and cumulative risk scoring across turns, triggering the `EmergencyKillSwitch` if drift exceeds confidence thresholds.

### Issue 17: Background Worker Integration for Emergency Kill Switch
- **Severity:** Medium
- **Location:** `agentwatch/worker/tasks.py`, `agentwatch/runtime/killswitch.py`
- **Description:** With PR #801 merged, the runtime possesses a thread-safe file sentinel and signal kill switch (`EmergencyKillSwitch`). However, Celery background workers (`agentwatch/worker/`) do not yet evaluate the kill switch before executing queued asynchronous jobs.
- **Why it matters:** Tripping the kill switch via `.panopti.kill` or POSIX signals stops the local process, but detached Celery worker nodes continue processing scheduled agent tasks.
- **Recommended fix:** Wire `EmergencyKillSwitch.verify()` into the Celery task base class or pre-run signal handler in `agentwatch/worker/tasks.py`, ensuring all asynchronous workers halt immediately when the sentinel is active.
