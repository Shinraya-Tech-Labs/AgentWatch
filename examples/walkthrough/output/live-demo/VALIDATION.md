# Live demo validation record

Recorded 2026-11-04 on the working tree based on `main` at `9627068`, on Windows 11 (AMD64, 16 logical CPUs) with Python 3.12.4.

| What was checked | Command | Result |
|---|---|---|
| Live demo with screenshots | `python examples/walkthrough/live_demo.py --fast --screenshots --out examples/walkthrough/output/live-demo` | Exit 0, 24 of 24 checks |
| Repeat runs | `python examples/walkthrough/live_demo.py --fast` | 5 of 5 runs passed all 24 checks |
| Legacy Windows console | Same, with `PYTHONIOENCODING=cp1252` | 24 of 24 checks, no crash |
| Pause between scenes | `--interactive`, Enter piped in | Exit 0, 24 of 24 checks |
| Approval in a real browser | `python examples/walkthrough/drive_dashboard.py --out examples/walkthrough/output/live-demo/dashboard-run` | Exit 0, 23 of 23 checks; results kept in `dashboard-run/` |
| Walkthrough | `python examples/walkthrough/run_walkthrough.py --out <dir>` | 3 of 3 runs exit 0, 23 checks each |
| Full Python suite, clean install | Fresh venv with only `pip install -e ".[dev]"`, then `pytest tests/ --cov=panoptis --cov-fail-under=70` with PostgreSQL and Redis | 3,412 passed, 0 failed, 12 skipped; 83.58% coverage |
| SQL erasure | `tests/test_sql_memory_erasure.py` in that run | 18 passed, 0 skipped: 9 on SQLite, 9 on PostgreSQL |
| Trajectory limits | `python scripts/mutation_check_trajectory.py` | 57 of 57 mutants caught |
| Framework integrations | `pytest tests/protect/test_framework_integrations.py tests/protect/test_generic_and_direct.py` on Python 3.14 with the frameworks installed | 41 passed |
| Coverage floors | `python scripts/coverage_gate.py` | Passed |
| Dashboard browser tests | `AGENTWATCH_E2E_DASHBOARD=1 pytest tests/e2e_dashboard` | 4 passed |
| Lint and format | `ruff check .` and `ruff format --check` | Clean |
| Types | `mypy --platform linux` over the Makefile module list | Clean, 135 files |
| Static security | `bandit -r panoptis panopti -ll` | No medium or high findings |
| Secrets | gitleaks on history; a separate pass on every changed and untracked file | No real secrets; synthetic fixtures only |

Check counts differ by mode for the reasons in "Check counts" in [the walkthrough README](../../README.md#check-counts). `terminal.png` was resized to 1,400 pixels wide after capture.

## Not validated

- **Claude Code against the real CLI.** The CLI on this machine is not signed in, so only the hook contract is tested.
- **Frontend type-check, build and Jest.** No frontend source changed, so these were not rerun.
- **GitHub CI on these changes.** Results above are local until the changes are pushed.
- **Linux or macOS demo runs.** The demo ran on Windows only here.

## Scope

The agent is scripted and no model is involved. The attack runs inside a temporary directory against a server on `127.0.0.1`. The API, pipeline, MCP gateway and SDK, evidence store, verifier and dashboard are the real product.

## Rerun after the persistence and API hardening

Recorded 2026-11-05 after the persistence and API hardening changed `panoptis/api/server.py`, on the same machine. The table above is the earlier run and its figures are kept as they were. The screenshots and transcripts in this folder come from that earlier run; the demos were rerun to a temporary folder.

| What was checked | Result |
|---|---|
| Full Python suite, clean `.[dev]` install, PostgreSQL and Redis | 3,574 passed, 0 failed, 12 skipped |
| Coverage | 83.79% of lines, 81.02% with branches counted; all 30 floors in `scripts/coverage_gate.py` pass on the line figure CI uses |
| Live demo | 3 of 3 runs passed 24 of 24 checks; the same with `PYTHONIOENCODING=cp1252` and with `--interactive` |
| Approval in a real browser | 23 of 23 checks |
| Walkthrough | 3 of 3 runs exit 0 |
| Framework integrations on Python 3.14 | 41 passed |
| Dashboard browser tests | 4 passed |
| Concurrent bursts | Per backend: 200 direct and 40 HTTP bursts of 20, in two runs on one database, all stored (`AGENTWATCH_BURST_REPEATS=100`) |
| Trajectory mutants | `python scripts/mutation_check_trajectory.py`: 57 of 57 caught |
| Persistence and API mutants | 83 defined, all apply and compile, all killed. The first pass had 1 invalid mutant, since rewritten, and 2 survivors, closed by new tests |
| Lint, format, types | Clean, mypy over 135 files |
| Static security | bandit: 0 high, 0 medium |
| Dependencies | pip-audit: no known vulnerabilities in 122 packages |

The 12 skips: 4 dashboard browser and 2 framework tests, run separately above; 2 Claude CLI tests; 2 real-model tests, whose credentials are missing or rejected; 2 that cannot run on Windows.
