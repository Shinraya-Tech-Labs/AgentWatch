# Erasure (right to be forgotten)

There are two erasure paths, one for each data model.

| Path | Data | Mechanism |
|---|---|---|
| `POST /api/v1/gdpr/erase` | v1 sessions, events, tasks, checkpoints, the in-memory trace cache, memory entries | rows are deleted; the response is a signed `ErasureReceipt` |
| `POST /api/v3/evidence/erase` | v3 immutable evidence | crypto-shredding: the subject's key is destroyed and derived data rebuilt. Observations stay in the Merkle chain as unreadable ciphertext, so integrity verification still passes (ADR-0011) |

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

## Unknown and foreign identifiers

Erasing an id that does not exist and erasing another tenant's id produce the same receipt (`items_erased: 0`, the same failed-target list). The response reveals nothing about who owns an id.

## Tests

- `tests/test_session_erasure_tenancy.py`: two tenants on a real (in-memory) SQLite database. It covers foreign versus owned erasure, related rows, events reusing a session id, the user scope, unknown keys, and every scope's counted-versus-erased totals.
- `tests/v3/test_tenant_isolation_api.py`: the HTTP endpoint end to end (the cache is evicted and the other tenant is untouched; a foreign erase looks like an unknown one) and v3 subject crypto-shredding per tenant.

## Known limitation

The `memory_and_causal` target imports a function that does not exist (`drop_memory_entries`), so it always reports a partial failure (HTTP 207). That is tracked as TO-031.
