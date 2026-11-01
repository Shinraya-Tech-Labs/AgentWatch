# Implementation Evidence Ledger

Task list: [`docs/MASTER_TASKS.md`](MASTER_TASKS.md). Each task records its changed files, the tests it added, the exact commands run with their results, its commits, and any remaining limitation.

Statuses: `NOT STARTED` · `IN PROGRESS` · `BLOCKED` · `FAILED` · `DONE`. A task is `DONE` only when tests exercise the behaviour; code inspection alone does not count (CTRL-004).

## CTRL-001 — Baseline

| Item | Value |
|---|---|
| Repository | https://github.com/sreerevanth/AgentWatch.git |
| Base branch / commit | `main` @ `8d5cf12e8ac948d265d274f09ab11a181c4a660d` |
| Work branch | `feat/protect-runtime-security` (CTRL-002) |
| Working tree at start | clean except the owner's uncommitted edit to `PROJECT_TASKS_TO_BE_DONE.md`; preserved and never staged |
| OS | Windows 11 Home (`10.0.26200`) |
| Python | 3.14.2 (default); 3.12.4 via `py -3.12` (the project pin is `>=3.12,<3.13`) |
| Node / npm | 22.23.0 / 11.12.1 |
| Docker | client 29.3.1; **daemon not running** (Compose smoke tests need Docker Desktop started) |
| PostgreSQL / Redis | not running locally (ports 5432 / 6379 closed); CI provides them |
| Claude Code CLI | 2.1.229 |
| Framework packages | `langgraph` 1.0.10, `langchain-core` 1.2.17 installed; AutoGen and smolagents **not installed** |
| Baseline tests | `pytest tests/`: **2226 passed, 8 skipped, 0 failed** (8 m 41 s); coverage 81.1 % of code on `main` |
| Baseline audit | [`docs/REPO_AUDIT.md`](REPO_AUDIT.md) |

## Status

| Task | Status | Evidence |
|---|---|---|
| CTRL-001 baseline | DONE | this section |
| CTRL-002 isolation | DONE | all work on `feat/protect-runtime-security`; `main` untouched |
| CTRL-003 ledger | IN PROGRESS | this file, kept current per task |
| CTRL-004 … CTRL-008 | IN PROGRESS | rules applied throughout; CTRL-008 runs at the end |
| TO-001 … TO-040 | NOT STARTED unless listed below | — |

## Pre-task work already on the branch

| Commit | Change | Tests |
|---|---|---|
| `3a8897a` … `9cb58bd` | `agentwatch/protect/`: model, argument analysis, threat classifier, output inspector, loop detector, intent grounding, RBAC, ledger, control/approvals, pipeline | `pytest tests/protect`: 55 passed (threat analysis and output inspection only; the other modules have no tests yet) |
| `6d6bb4d` | `docs/REPO_AUDIT.md` | n/a |

## Task log

<!-- one section per task, appended as work completes -->
