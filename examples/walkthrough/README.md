# End-to-end walkthrough

One script starts a clean local AgentWatch deployment and drives the product through every protection it claims. Each step checks its own outcome and the run stops at the first failed check. It is reproducible: no network services, no API keys of yours, no paid calls. Every tool is a harmless stub, and "dangerous" commands are analysed, never executed.

```bash
python examples/walkthrough/run_walkthrough.py --out walkthrough-out
```

Add `--screenshots` to also start the dashboard and capture it (see [Setup](#setup)).

The committed output of a real run is in [`output/`](output/):

- `walkthrough.log` (human-readable);
- `walkthrough.json` (structured);
- `console.txt` (the console);
- `screenshots/`.

## What it does

A temporary deployment directory holds the following:

- the SQLite evidence store (`AGENTWATCH_STORE`);
- the kill file (`AGENTWATCH_KILL_FILE`);
- the evidence anchor directory;
- an RBAC policy with two roles, `coder` and `researcher`, plus a tenant binding.

The real API server (`uvicorn panoptis.api.server:app`) runs on a free local port. Two tenant keys are configured, `acme` and `globex`, plus an operator key. The agents run in the script's process and share the store with the API, as a real deployment would.

| # | Step | How the outcome is checked |
|---|---|---|
| 1 | an allowed call | the tool returns, and the running API reports the decision as `allow` |
| 2 | `rm -rf /` blocked before execution | a side-effect marker file gets no new line: the tool body never ran |
| 3 | a force-push escalated to a human | the request appears in the API as pending; nothing runs while it waits; it is approved over the API with its action digest; the tool then runs exactly once |
| 4 | an injected line in a tool's output | the agent receives the result with the instruction line replaced by a marker and the rest kept |
| 5 | five identical calls | 2 run, then `DUPLICATE_CALL_LOOP`, then the session's circuit breaker opens (`CIRCUIT_OPEN`) |
| 6 | a destructive call under the objective "Summarize README.md" | refused with `GOAL_ACTION_MISMATCH` from the intent stage |
| 7 | `researcher` calls `bash` | refused with `RBAC_TOOL_FORBIDDEN`; the API's dry-run check returns the same answer |
| 8 | the MCP Python SDK client → `panoptis.protect.mcp_gateway` → an MCP server | the allowed call returns; the blocked call is answered by the gateway and is absent from the server's own call log; the injected line in a result is removed |
| 9 | an MCP client config with a direct server and a gateway-wrapped one | `UNMONITORED` vs `MONITORED`; after `wrap_server(apply=True)` the direct one becomes `MONITORED`; the inventory is recorded for the dashboard |
| 10 | evidence | the store verifies; the standalone verifier (`python -I panoptis/evidence/verifier.py`) accepts the exported bundle; a copy of the database with `block` rewritten to `allow` (triggers dropped) is rejected |
| 11 | two tenants | `globex` sees only its own decision, gets 404 on `acme`'s approval, and gets 403 when it tries the global emergency stop |
| 12 | `--screenshots` | the production dashboard build, signed in with the `acme` key, captured on 7 pages |

### What the steps show, precisely

- **Step 5: loop detection escalates.** The two `DUPLICATE_CALL_LOOP` calls are blocked because this pipeline has no approval channel (`NO_APPROVAL_CHANNEL`). With an approval channel they would wait for a human instead. The decisions page in the screenshots shows this.
- **Step 3: the recorded verdict is `allow`.** The escalated call's final verdict is `allow`, because it was approved. The approval record holds the approver, `acme:alice`.
- **Out of scope: the Claude Code hook.** The walkthrough does not run Claude Code. The hook has not been verified against a real, signed-in CLI (TO-012 is BLOCKED; see `IMPLEMENTATION_EVIDENCE.md`).

## Setup

- **Python 3.12.** Install with `pip install -e ".[dev]"`. The MCP step needs the `mcp` package, which is a core dependency.
- **For `--screenshots`:**
  - Node 20+ and a dashboard build: `cd frontend && npm ci && npm run build`.
  - Playwright's Chromium: `python -m playwright install chromium-headless-shell`.

  Without a build, the script says so and skips the screenshots.

## Options

| Option | Meaning |
|---|---|
| `--out DIR` | where the log, JSON and screenshots go (default `walkthrough-out`) |
| `--workdir DIR` | the deployment directory to create; it must not exist. The default is a new temporary directory |
| `--keep` | keep the deployment directory (the evidence store, policy, kill file, exported bundle and tampered copy) for inspection |
| `--screenshots` | also start the dashboard and capture it |

## Reset

Nothing persists outside `--out` unless you pass `--keep`. The deployment directory is deleted at the end, and the servers are stopped, including the Next.js child processes on Windows. To start over, delete the `--out` directory. With `--keep`, also delete the deployment directory printed at the start of the run.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `http://127.0.0.1:<port>/health did not come up` | The API failed to start. Run `python -m uvicorn panoptis.api.server:app` yourself to see the error, which is usually a missing dependency: reinstall with `pip install -e ".[dev]"`. |
| `CHECK FAILED: …` | A protection did not behave as documented. This is a real failure: the log names the step and the check. Please report it with `walkthrough.json`. |
| step 8 hangs or fails to import `mcp` | Install the core dependencies (`mcp>=1.8`). |
| `screenshots skipped` | Build the dashboard first (see [Setup](#setup)). |
| Playwright `Executable doesn't exist` | Run `python -m playwright install chromium-headless-shell`. |
| `FileExistsError` for `--workdir` | The directory exists. Choose a new one; the script never reuses or deletes an existing directory. |
| `DATABASE_URL` set in your shell | The script removes it from the server's environment, so the walkthrough always uses its own SQLite store. |
