# Right to erasure: memory

`POST /api/v1/gdpr/erase {"identifier": "...", "scope": "user_id|agent_id|session_id|tenant_id"}` erases a subject's data from every registered target. It returns an HMAC-signed receipt, and HTTP 207 if any target failed. This page covers the **memory** target. The other targets are the trace cache and the SQL sessions and events.

Code: `panoptis/memory/governance.py` (`register_memory_store`, `list_memory_entries`, `drop_memory_entries`), `panoptis/memory/engine.py`, `_MemoryErasureTarget` in `panoptis/api/server.py`. Tests: `tests/test_memory_erasure.py`.

## Behaviour

- **Every memory engine is erasable.** `MemoryEngine(tenant_id=...)` registers its store with the erasure registry of its process. Stores are held weakly, so a discarded engine drops out.
- **The target exists only when memories do.** The memory target is added to an erasure only when at least one memory store exists in the process. With none, the process holds no memory records, and the receipt does not claim a memory target.
- **Matching is exact, per scope:**

  | scope | matches |
  |---|---|
  | `user_id` | `entry.metadata["user_id"]` |
  | `agent_id` | `entry.agent_id` |
  | `session_id` | `entry.session_id` |
  | `tenant_id` | every entry of the tenant |

  It used to match `user_id` by substring, so `u-1` also matched `u-10`.
- **It is scoped to the caller's tenant.** Only the stores registered for the authenticated tenant are searched. A `tenant_id` erasure of another tenant matches nothing.
- **A failing store is a visible partial failure.** It appears in the receipt's `failed_targets` (`memory`) and the endpoint answers 207.
- **The evidence ledger is not touched.** The append-only, Merkle-sealed evidence is never edited by this endpoint. Its subject erasure is crypto-shredding (the subject's data key is destroyed; see [EVIDENCE_INTEGRITY.md](EVIDENCE_INTEGRITY.md)), so integrity verification keeps passing.

## Also fixed

In self-hosted multi-tenant mode (`AGENTWATCH_TENANT_KEYS`), the SQL sessions-and-events target ran without a tenant filter, so a tenant key could reach other tenants' rows. It is now scoped to the authenticated tenant. Cloud mode was already scoped.

## Limitations

- **Only memories of this process.** Memories held by MemoryEngines in *other* processes (for example agents running elsewhere) are not reachable from the API process. Erase them where they run, with `drop_memory_entries(...)`. The endpoint does not claim them.
