# Pre-execution threat analysis

The threat stage of the protection pipeline inspects every tool call before it runs. It covers:

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
