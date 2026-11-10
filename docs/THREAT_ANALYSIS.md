# Pre-execution threat analysis

The threat stage of the protection pipeline inspects every tool call that reaches it through an enforcing integration, before it runs. It covers:

- the tool name and every string in the arguments, found by walking nested dicts and lists;
- base64 and URL-encoded values, which are decoded and analysed as well.

Code: `panoptis/protect/arguments.py` (structured analysis) and `panoptis/protect/threats.py` (secrets, forwarded injections, and the legacy pattern library). Tests: `tests/protect/test_threat_analysis.py`.

## How arguments are understood

What the analyser does with an argument depends on its key and on the tool's name (`bash`, `run_python`, `sql_query`, `fetch_url` and so on).

| Content | How it is analysed (not just string matching) |
|---|---|
| **Shell** | Quote-aware split into simple commands across `;`, `&&`, `\|\|` and `\|`. It descends into `$(…)` and backtick substitutions, `bash -c` / `powershell -Command` / `-EncodedCommand` payloads, `find -exec`, and text piped into a shell, including through `base64 -d`. Wrappers (`sudo`, `env`, `timeout`, `xargs`, `nohup`, …) are unwrapped; a privileged wrapper marks the command privileged. Same-line `x=…` assignments resolve `$x`. Each command is then classified from its own name, flags and operands. |
| **Python** | Parsed with `ast`. Import aliases are resolved (`from os import system as go` → `os.system`). Dangerous calls are matched by qualified name, and string or list arguments to `os.system` / `subprocess.*` are analysed as shell. |
| **SQL** | Split into statements, then classified per statement: unbounded `DELETE`/`UPDATE`, `DROP`, `TRUNCATE`, privilege changes, file and program access, stacked queries, tautologies. |
| **Paths** | System and sensitive locations (`~/.ssh`, `.env`, cloud credentials, `/etc/shadow`, …), and deep traversal. |
| **URLs and hosts** | The host is canonicalised first: `2852039166`, `0xa9fea9fe`, `0251.0376.0251.0376` and `::ffff:169.254.169.254` are all 169.254.169.254. Then it is checked for the cloud metadata service, private and loopback addresses, and known paste, tunnel and webhook-capture services. A `file://` URL on a network tool is flagged. |
| **Secrets** | Provider token patterns and sensitive keys. A secret going to its own credential parameter is allowed. A secret anywhere else is redacted before the tool runs (`MUTATE_AND_ALLOW`), and a secret sent to a network destination is blocked. |

## Detector families

| Family | Examples of codes | Default |
|---|---|---|
| Destructive filesystem | `FS_DELETE_CRITICAL`, `FS_DELETE_RECURSIVE`, `FS_SHRED`, `DISK_RAW_WRITE`, `DISK_FORMAT`, `OVERWRITE_SYSTEM_FILE`, `PY_RMTREE_CRITICAL`, `PY_DELETE_SYSTEM` | block when a system path is involved; escalate for a scoped recursive delete |
| Remote code execution | `RCE_DOWNLOAD_PIPE_SHELL`, `RCE_POWERSHELL_IEX`, `REVERSE_SHELL`, `PY_SHELL_EXEC`, `PY_UNSAFE_DESERIALIZE`, `DYNAMIC_COMMAND` | block; escalate for a dynamic command or unsafe deserialization |
| Privilege escalation | `PRIVILEGE_ESCALATION` (sudo/doas/su, including `sudo su -` and `sudo -i`), `SETUID_BIT`, `PRIVILEGE_GRANT` (admin groups, sudoers), `PERMISSIONS_*`, `SQL_PRIVILEGE_CHANGE` | escalate; block for setuid and recursive system permission changes |
| Credential exposure and exfiltration | `EXFIL_SENSITIVE_READ_AND_SEND`, `EXFIL_ENVIRONMENT`, `SECRET_EXFILTRATION`, `EXFIL_ENDPOINT`, `SECRET_IN_ARGUMENT` | block; escalate for capture endpoints; mutate a secret in a local argument |
| Network trust boundary | `SSRF_METADATA`, `SSRF_PRIVATE`, `LOCAL_FILE_URL` | block for metadata; flag private addresses; escalate local files |
| Persistence and host control | `PERSISTENCE`, `HOST_SHUTDOWN`, `KILL_INIT`, `PROCESS_KILL`, `FORK_BOMB` | escalate persistence and shutdown; flag process kills; block a fork bomb or killing init |
| Data stores | `SQL_DELETE_ALL`, `SQL_DROP`, `SQL_TRUNCATE`, `SQL_UPDATE_ALL`, `DATASTORE_FLUSH`, `SQL_FILESYSTEM` | block; escalate for an unbounded UPDATE |
| Version control | `GIT_FORCE_PUSH`, `GIT_DISCARD_WORK` | escalate a force push; flag discarded work |
| Injection forwarded in arguments | `INJECTION_IN_ARGUMENT` | escalate |
| Legacy pattern library | `PATTERN_*` | its own risk levels |

## Risk score and verdict

- **Finding:** each finding has a severity, a recommended verdict, an explanation (`message`) and evidence (the argument path and the command or target).
- **Verdict:** the decision's verdict is the strongest recommendation, in the order BLOCK > ESCALATE_HITL > MUTATE_AND_ALLOW > ALLOW.
- **Risk score:** a noisy-OR of the severities, where INFO = 0, LOW = 0.2, MEDIUM = 0.5, HIGH = 0.8 and CRITICAL = 1. Independent signals compound, and one critical finding scores 1.0. The score explains a decision; it does not decide it.
- **Reason:** the decision's `reason` names the deciding stage and rule, for example `[threat:FS_DELETE_CRITICAL] Recursive/forced deletion of a system or home path: /`.

## Evidence

Every recorded decision carries:

- `findings`, each with code, severity, message, evidence and recommended verdict;
- `risk_score` and `reason`;
- `policy_version`: the digest of the RBAC policy in force;
- `threat_rules_version`: a content hash of the rule code (`panoptis/protect/arguments.py`, `threats.py` and the legacy pattern library), so a decision names the exact rules that made it.

## Failure behaviour

If a detector raises, the stage's result is a `STAGE_ERROR` finding, and the call is blocked (`ProtectionConfig.fail_closed=True`, the default). With `fail_closed=False` the stage error is downgraded, but arguments that were never analysed cannot be authorised either, so the call is still blocked (`NO_PROFILE`). Tests: `test_a_crashing_detector_fails_closed`.

## False positives

The analyser prefers escalation, which asks a human, over blocking whenever intent is ambiguous. Known and accepted behaviours:

- **Scoped cleanups escalate rather than block:** for example `rm -rf ./build dist`. A recursive delete of a system or home path blocks.
- **Any `sudo` escalates,** including `sudo apt-get install jq`. Running with elevated privileges is a decision for a human, whatever the command.
- **Commands inside data are not commands:** `grep -rn 'rm -rf /' docs/` and a README that mentions `curl … | sh` are not flagged. Shell analysis applies to shell arguments only.
- **Secrets in local writes are redacted, not blocked.** The tool runs with `[REDACTED:…]`; this is `MUTATE_AND_ALLOW`.
- **Benign near-misses are tested to stay clean:** `chmod 755` / `0644` / `u+x`, a bare `env`, `env | grep PATH`, `base64 -d` on its own, adding a user to the `docker` group, relative-path deletes in Python, and public IP addresses.

Three limitations remain:

- **Same-line variables only:** a variable whose value is not assigned on the same line cannot be resolved. When it is the command name it escalates (`DYNAMIC_COMMAND`); in an operand it is treated as an ordinary path (`rm -rf ./$TARGET` is a scoped delete).
- **Code the call obtains later is invisible:** a script that the command downloads or reads at run time cannot be checked before it runs.
- **No interpreter emulation:** the analysis is static. Obfuscation that needs evaluation to decode, beyond base64, URL encoding and PowerShell `-EncodedCommand`, is not decoded. Downloading into an interpreter is blocked regardless.

To add your own rules, pass `ThreatAnalyzer(extra_patterns=[...])`, or restrict tools and arguments per role in the RBAC policy.

## Trajectory state limits

The opt-in trajectory verifier keeps two kinds of state apart.

- **Step history** is ordinary. The rules never read it, so it can always be evicted.
- **Session facts** hold everything the rules need: the first sensitive read, the first untrusted input, and the code indicators in each file. Only sessions with something relevant have them. A file with no indicator, whose last characters could not begin one, costs nothing.

All of it is bounded, and each limit fails towards refusing:

| Limit | Default | Applies to | When it is reached |
|---|---:|---|---|
| `max_steps_per_session` | 2,000 | One session | Older history is dropped. |
| `max_files_per_session` | 1,024 | One session | Later writes merge into one entry, so their indicators still combine. Execution in that session needs a person (`LTL_FILE_TRACKING_OVERFLOW`). |
| `max_tracked_files` | 8,192 | One tenant | Same as above, for the session that reached it. |
| `max_facts_per_tenant` | 4,096 | One tenant | The tenant's least recently active session is sealed. |
| `sealed_filter_bits` | 2,097,152 (256 KiB) | One tenant | Fixed size. As it fills, more of that tenant's new sessions are wrongly treated as sealed. |
| `max_tenants` | 256 | All tenants | A tenant with no place is refused any step that would create security state (`LTL_STATE_OVERFLOW`). |
| `max_sessions` | 5,000 | All tenants | Step history is dropped from the tenant with the most sessions. Security is unaffected. |
| `max_history_steps` | 50,000 | All tenants | Step history is dropped from the tenant with the most steps. Security is unaffected. |

### Sealing

Facts are never dropped silently. When a tenant's table is full, the session it has not heard from for longest is sealed: its exact facts are replaced by an entry in that tenant's fixed-size Bloom filter, and a warning is logged.

From then on the session is judged as if it had done everything the rules look for. The rules only get stricter as facts are added, so sealing can never weaken a verdict. Its shell commands, network calls and writes to startup files are refused with `LTL_STATE_OVERFLOW`. Reads and ordinary file writes, which no rule covers, still work.

New sessions keep working, so a flood of sessions does not stop a tenant taking new work.

The filter has no false negatives, so a sealed session is always recognised. It does have false positives: a new session can be treated as sealed, and should then start again under a new session id. A session that already has an exact record is never affected. Measured on the default size:

| Sessions sealed | Filter fill | False positives |
|---:|---:|---:|
| 10,000 | 1.9% | under 0.001% |
| 100,000 | 17.4% | 0.1% |
| 250,000 | 37.9% | 2.1% |
| 500,000 | 61.5% | 14% |
| 1,000,000 | 85.2% | 53% |

### Tenants

A tenant takes a place in the table only when a step would create security state: a sensitive read, untrusted input, or a write that holds an indicator or ends in the start of one. `verify_action` reserves the place before the step runs, so a step that was allowed always has somewhere to record what it did. A place is kept until `release_tenant`, because displacing a tenant could lose a step it has just run.

When the table is full, a tenant with no place is refused those steps, so nothing is ever learned and then lost. It has no facts, so everything else it does is as safe as it looks: a command, a network call or a file with nothing relevant in it still runs. Tenants already in the table are not affected.

Set `max_tenants` to at least the number of tenants one process serves that may ever create security state. A place costs nothing until it is used.

### Capacity recovery

| How | Gives back | Who |
|---|---|---|
| A file is overwritten with nothing relevant | The path, and the session's record if that was the last fact | Automatic |
| A new session needs room | A record, by sealing the oldest session | Automatic |
| `verifier.release_session(session_id, tenant_id=...)` | Everything that session held | The host, when a session has really ended |
| `verifier.release_tenant(tenant_id)` | Everything the tenant held, including its filter and its place | An operator, accepting that those sessions are forgotten |

Nothing expires with time, because an attacker could simply wait. `release_session` cannot unseal a session. Neither release call may be offered to an agent. No integration calls them yet, so a host that wants records back when sessions end must call `release_session` itself. `verifier.capacity(tenant_id)` reports how much of a budget is in use.

### Measured memory

These are operating-system figures from fresh processes at the default limits, on Python 3.12 and Windows 11. They cover the verifier only, not the rest of the API process. Run `python -m benchmarks.trajectory_memory` to measure your own platform. The [full report](https://github.com/Shinraya-Tech-Labs/AgentWatch/blob/main/benchmarks/results/trajectory_memory/REPORT.md) has every scenario.

| Load | Resident memory added |
|---|---:|
| One tenant with every budget full (4,096 sessions, 8,192 paths of 256 characters, its filter) | 14.7 MiB |
| 16 tenants of ordinary coding sessions (65,536 sessions) | 65.5 MiB |
| All 256 tenants with every budget full (1,048,576 sessions, 2,097,152 paths) | 1,889 MiB |
| The history limit full of the largest possible steps | 696 MiB |
| **All of the above at once, the worst case the limits allow** | **2,602 MiB** |

### Supported workload

One process at the default limits supports:

| Dimension | Supported | Evidence |
|---|---:|---|
| Tenants that create security state | 256 | Measured full |
| Sessions with exact facts | 4,096 per tenant, 1,048,576 in all | Measured full |
| Tracked paths | 1,024 per session, 8,192 per tenant | Measured full |
| Sealed sessions | 100,000 per tenant at 0.1% false positives | Measured; plan a `release_tenant` before 250,000 |
| Step history | 5,000 sessions or 50,000 steps | Measured full |
| Verify and record | About 22,000 steps per second | p50 37 µs, p99 62 µs, with the table full and every new session sealing one |
| Memory to provision | 2.6 GiB worst case, about 4 MiB per active tenant in ordinary use | Measured |

Two limits on reading those figures:

- **Throughput is for the whole process.** One lock serializes every tenant, so a very busy tenant slows the others. It cannot use up their state, but it does share the CPU.
- **Beyond this, add processes.** Raising the limits raises the worst case in proportion, and processes do not share state, so route a session to one process for its whole life.

### What this does not protect against

- **Within a tenant, a flood can seal other sessions.** Anyone able to open sessions in a tenant can push that tenant's idle sessions out, and those sessions are then refused. Other tenants are not affected.
- **Saturating the filter locks a tenant's new sessions.** After far more sealed sessions than the table above shows, nearly every new session in that tenant is refused until an operator calls `release_tenant`.
- **Tenant isolation relies on the tenant id being genuine.** It comes from the authenticated principal. An integration that let an agent choose it could fill `max_tenants`.
- **State lives in one process.** A restart forgets everything, including sealed sessions, and several workers do not share state.

## Known limitations of the API

These came out of adversarial review and stay open. Each has a stated reason, and none is hidden behind a passing test.

| Limitation | Effect | Why it stays |
|---|---|---|
| Approvals have no approver role | Any key of a tenant can approve that tenant's pending requests, and `decided_by` is chosen by the caller (the ledger records it under the tenant) | Needs a role model that key scopes do not yet provide. Keep approval keys away from the agents they govern |
| `Mcp-Session-Id` is chosen by the client | A client that rotates it starts with empty trajectory state, so a sequence split across sessions is not linked | A gateway cannot tell a new session from an evasive one. Enforce per-agent or per-tenant limits as well |
| A retried event repeats its side effects | A retry after a 503 adds to the spend budget again and may repeat an alert. Totals in the session row do not change | Errs towards stopping early, and telling a retry from a first delivery needs a durable record of side effects |
| A race can still leak a refused event | Two sessions racing for one `event_id` both pass the early check; the loser gets 409 after subscribers saw it. The database still stores one row | Closing it means writing before publishing, which changes the delivery order for every subscriber |
| Session ids can be reclaimed after erasure | Erasure and pruning delete the row, so another tenant may then use the id | Keeping a tombstone would keep data that erasure promises to remove |
| `POST /api/v1/sessions` confirms an id is taken | It answers 409 for an id another tenant owns, so a tenant that guesses an id learns it exists. It learns nothing about the owner or the session. Reads and event writes answer 404 and confirm nothing | A client must know its session was not created, and ids are global. Use random session ids |
| Reads come from memory | v1 reads use the collector, so stored sessions answer 404 after a restart, though ownership is still decided by the database | The database holds fewer fields than the collector. Reading through it is a larger change |
| `String(36)` session ids | On PostgreSQL, a session id over 36 characters fails the write and answers 503. So does a tenant id over 36 characters on a database that has not run the [one-time migration](PERSISTENCE.md#widening-tenant-columns). Nothing is lost silently | Widening them needs a migration, which is never automatic |
| No database, no durable owner | Without a database, once a session leaves the collector any tenant can register its id. It reads none of the old session's data, and the first owner gets 404 | Ownership needs a durable store: multi-tenant deployments should configure a database |
| `/health` counts span tenants | The public health response shows global trace and budget counts, never ids or amounts | Monitoring needs them |
| One quota behind an unlisted proxy | Behind a proxy not in `AGENTWATCH_TRUSTED_PROXIES`, every anonymous client shares a quota. Entries must be exact addresses | Trusting the header by default is the bypass this replaced |
| The default quota is 100 requests per hour | Each principal or address gets this, below the 200 per minute route limit, so a busy agent gets 429 and the forwarder loses those events (it counts them as `refused`) | Raising a security limit by default is a deployment decision. Set `RATE_LIMIT_PER_USER` for the real ingest rate |
| A request with any `X-Api-Key` skips the global limit | A distributed flood with invented keys is limited per address only. The route still answers 401 | The limiter cannot verify a key cheaply, and counting them would let a flood lock out tenants |
| Bucket eviction is a scan | Above 50,000 live buckets each hit scans them, measured at about 9 ms | Needs 50,000 distinct addresses inside a window |
| Rate limits are per process | The Redis backend exists but the API does not use it, so replicas each keep their own counts | Wiring it needs a decision on failure behaviour when Redis is down |
