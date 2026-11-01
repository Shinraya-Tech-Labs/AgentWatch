# Persistence and database failure

The API can run with or without a database. The rule (TO-007): **a configured database that fails never turns into a silent in-memory deployment.**

| Situation | Behaviour | `/health` |
|---|---|---|
| no `DATABASE_URL` | in memory by design: data lives in the process and is lost on restart | 200, `persistence.state: not_configured`, `database.status: in_memory` |
| `DATABASE_URL` reachable | writes go to the database | 200, `state: connected`, `database.status: ok` |
| `DATABASE_URL` unreachable at startup | **the API does not start** (startup error) | n/a |
| … with `AGENTWATCH_ALLOW_MEMORY_FALLBACK=1`, outside production only | starts in memory; every write is answered **503 "not persisted"** | **503**, `state: fallback`, `database.mode: memory_fallback` |
| a database write fails while running | that request is answered **503 "not persisted"**; the failure is logged at ERROR | 503 while the database ping fails |
| the database comes back | writes are accepted again with no restart; the pool reconnects (`pool_pre_ping`) | 200 |

Production (`AGENTWATCH_ENV=production`) refuses the memory fallback even when the flag is set.

Other stores:

- **Tenants and API keys:** `SqlTenantStore` denies a key when the database cannot be read, and raises when it cannot be written (see [`TENANCY.md`](TENANCY.md)).
- **GDPR erasure:** reports a per-target failure with HTTP 207 (see [`ERASURE.md`](ERASURE.md)).
- **v3 evidence:** writes go to its own store, `AGENTWATCH_STORE`.

## Recovery

1. `/health` shows `database.status: degraded` or `unavailable`.
2. Restore the database, or fix the network or credentials. A running API resumes writing on its own. An API that refused to start needs a restart once the database is reachable.
3. Writes answered 503 during the outage were not stored. Clients should retry them: the event ingestion route is safe to retry because events carry their own `event_id`.
