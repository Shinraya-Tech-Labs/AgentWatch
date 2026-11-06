# Erasure (right to be forgotten)

There are two erasure paths, one for each data model.

| Path | Data | Mechanism |
|---|---|---|
| `POST /api/v1/gdpr/erase` | v1 sessions, events, tasks, checkpoints, cost budgets, the in-memory trace cache, memory entries | rows are deleted; the response is a signed `ErasureReceipt` |
| `POST /api/v3/evidence/erase` | v3 immutable evidence | crypto-shredding: the subject's key is destroyed and derived data rebuilt. Observations stay in the Merkle chain as unreadable ciphertext, so integrity verification still passes (ADR-0011) |

## What a receipt covers

A receipt covers exactly what it names, and nothing else:

- **`targets_reached`:** targets that completed. A target that matched nothing still counts as reached.
- **`failed_targets`:** targets that errored or could not delete every match. The endpoint answers 207.
- **`not_reached`:** what this erasure did not cover. It always lists memory stores in other processes, traces, cost budgets and checkpoints held by other API processes, git branches created for checkpoints, the operator's in-memory buffer of recent events, the governance audit log, the evidence ledger, and backups, exports and copies outside the deployment. It also says when no database or no in-process memory store is configured here. With several tenants it lists rows stored before rows carried a tenant, which a tenant's erasure cannot reach until they are [given their owner](PERSISTENCE.md#upgrading-a-cloud-deployment).

`not_reached` is covered by the signature. A receipt is not a statement that every copy of the data is gone, and erasure is not a claim of GDPR compliance.

## Tenant rule

Both paths act only inside the caller's tenant, taken from the credential (see [`TENANCY.md`](TENANCY.md)).

- **Database:** every v1 `DELETE` carries the tenant constraint itself, through `Repository.erase_session`. There is no fetch-then-delete window.
- **Events:** another tenant's events that reuse the same session id are not touched.
- **Scopes:** a `tenant_id` scope can only name the caller's own tenant.
- **v3:** keys are per (tenant, subject). Erasing `alice` in tenant A leaves tenant B's `alice` readable.

## v1 scopes

| Scope | Matches (within the caller's tenant) |
|---|---|
| `session_id` | that session |
| `agent_id` | sessions whose `agent_id` matches |
| `user_id` | sessions whose recorded `session_metadata.user_id` matches (there is no user column) |
| `tenant_id` | every session of the caller's tenant; another tenant's id matches nothing |

Each matched session is erased with its events, tasks and checkpoints, and is evicted from the trace cache together with its persisted trace file. An unknown filter key matches nothing; it is never silently dropped, which would widen the match.

The `runtime_state` target removes the session's cost budget and the caller's checkpoints, metadata and snapshots included. It runs first, because the rows it reads to find the sessions are what the other targets delete. It reports a failed target if a checkpoint file could not be deleted, if the database could not list the subject's sessions, or if a matched session has a checkpoint with no tenant. Such a checkpoint belongs to `default`, so another tenant's erasure leaves it in place.

Limits of this target:

- Later targets still run after it fails. If the subject's sessions were found through `agent_id` or `user_id`, a retry cannot find them again, so retry a failed erasure by `session_id`.
- With no database, an `agent_id` or `user_id` erasure finds only sessions still in the trace cache. A budget it cannot find expires after its time to live. A checkpoint it cannot find stays until an operator prunes it.
- An event that arrives during an erasure can recreate the session's trace and budget.

## Unknown and foreign identifiers

Erasing an id that does not exist and erasing another tenant's id produce the same receipt (`items_erased: 0`, the same failed-target list). The response reveals nothing about who owns an id.

## Tests

- `tests/test_session_erasure_tenancy.py`: two tenants on a real (in-memory) SQLite database. It covers foreign versus owned erasure, related rows, events reusing a session id, the user scope, unknown keys, and every scope's counted-versus-erased totals.
- `tests/test_runtime_tenant_isolation.py`: cost budgets and checkpoints, on SQLite and PostgreSQL, including failed deletes.
- `tests/v3/test_tenant_isolation_api.py`: the HTTP endpoint end to end (the cache is evicted and the other tenant is untouched; a foreign erase looks like an unknown one) and v3 subject crypto-shredding per tenant.

## Known limitation

The receipt reports what this process reached. Other API processes hold their own traces, budgets and checkpoints, which an erasure here cannot reach.
