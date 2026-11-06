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

## Session ownership

A session id belongs to one tenant for good. `agent_sessions.session_id` is a global primary key, and the first tenant to commit an id owns it.

**The database decides, not the API process.** A session write is one atomic statement, `INSERT ... ON CONFLICT DO UPDATE ... WHERE <same tenant> RETURNING`. No row back means another tenant owns the id. There is no gap between looking for the row and writing it, so this holds after a restart, across workers and when two tenants create the same id at the same moment.

| A tenant uses another tenant's session id | Answer | What is stored |
|---|---|---|
| `POST /api/v1/sessions` | **409** `session id unavailable` | nothing changes |
| `POST /api/v1/events` | **404**, which does not confirm the id exists | nothing changes |
| An in-process publisher, with no request to answer | logged at ERROR and counted in `events_not_stored` | nothing changes |

The routes ask the database before anything else sees the request, so a refused write leaves nothing in the in-memory collector.

The 409 tells the caller that the id is taken, which [THREAT_ANALYSIS.md](THREAT_ANALYSIS.md) lists as an accepted disclosure.

Two triggers enforce the same rule for any code path. They are added on every start, so an existing database gets them too:

- `agent_sessions_tenant_fixed`: a session's `tenant_id` cannot change once it is set.
- `agent_events_tenant_matches`: an event's tenant must be its session's tenant.

A row with no tenant belongs to `default`, as everywhere else. A single-tenant deployment stores no tenant at all and is unaffected. Turning on tenant keys does not hand existing rows to any tenant.

### Upgrading a cloud deployment

Cloud mode used to store session and event rows with no tenant. Those rows now belong to `default`, so a tenant's later writes to one of those sessions are refused, and its erasure cannot reach them. Erasure receipts in cloud mode say so under `not_reached`.

To give old rows their owner, run this once on PostgreSQL. The server has set `tenant_id` in session metadata since TO-003, so review any older rows before trusting the value:

```sql
UPDATE agent_sessions SET tenant_id = session_metadata->>'tenant_id'
WHERE tenant_id IS NULL AND session_metadata->>'tenant_id' IS NOT NULL;

UPDATE agent_events AS e SET tenant_id = s.tenant_id
FROM agent_sessions AS s
WHERE e.session_id = s.session_id AND e.tenant_id IS NULL AND s.tenant_id IS NOT NULL;
```

The trigger allows this because it only refuses changing a tenant that is already set.

### Widening tenant columns

A tenant id can be 64 characters, the limit of `AGENTWATCH_TENANT_KEYS` and of the v3 tables. `agent_sessions`, `agent_events` and `memory_entries` used to hold 36. New databases get 64. **An existing PostgreSQL database keeps 36 until an operator widens it: the server never migrates production data.** It logs a warning at startup until then, and a write for a longer tenant id fails with a 503. Nothing is truncated, and ids of 36 characters or fewer are unaffected.

Run this once, in a quiet period: the `ALTER` statements take a brief exclusive lock. It rewrites no rows and changes no stored value. The trigger has to be recreated because PostgreSQL refuses to alter a column a trigger names, and the transaction means the rule is never off:

```sql
BEGIN;
DROP TRIGGER agent_sessions_tenant_fixed ON agent_sessions;
ALTER TABLE agent_sessions ALTER COLUMN tenant_id TYPE VARCHAR(64);
ALTER TABLE agent_events ALTER COLUMN tenant_id TYPE VARCHAR(64);
ALTER TABLE memory_entries ALTER COLUMN tenant_id TYPE VARCHAR(64);
CREATE TRIGGER agent_sessions_tenant_fixed
    BEFORE UPDATE OF tenant_id ON agent_sessions
    FOR EACH ROW EXECUTE FUNCTION agentwatch_session_tenant_fixed();
COMMIT;
```

SQLite does not enforce the width, so it needs no migration. The cloud tenant store keeps 36 characters: its tenant ids are generated UUIDs.

## Event delivery and ordering

**Uniqueness.** `event_id` is a primary key, and the write is atomic, so a burst of copies stores one row.

| The same `event_id` again | Result |
|---|---|
| Same tenant and session | Accepted and stored once. The first write wins, so a retry is always safe |
| Another tenant or session | **409** `event id unavailable`, asked before anything sees the event, so a refused event reaches no subscriber. Accepting it would lose the event while telling the sender it was stored |

**No silent loss.** A write that loses to contention (a deadlock, a serialization failure, a locked SQLite file) is tried up to 3 times. After that, or on any other failure:

| Publisher | What happens |
|---|---|
| An HTTP request | **503**, and the client should retry with the same `event_id` |
| In-process, with no request | Logged at ERROR with the event id and counted in `/health` under `persistence.events_not_stored`. **The event is not queued or retried**, so a non-zero count means events are missing and nobody was told |

**Ordering.** Concurrent events commit in any order, so commit order is not send order. Events are always read back by `step_number`, then `timestamp`, then `event_id`. A client that needs an exact order must set `step_number`, which defaults to 0.

**The session row.** Concurrent writers can hold older copies of a session, so an update can never move the row backwards:

| Field | Rule |
|---|---|
| `total_events`, `total_tokens`, `estimated_cost_usd` | Only ever rise |
| `agent_name`, `goal`, `ended_at`, `final_confidence` | Can be set, never cleared |
| `status` | Ignored from a write that does not know the session has ended |
| `started_at`, `tenant_id` | Fixed by the first write |
| Everything else, including `session_metadata` | The last committed write wins |

These totals are a summary written from the collector, and can trail the events by a write. Count the events for an exact figure.

## Shutdown

Publishers that do not await, such as the framework adapters, have writes in flight when the API stops. Shutdown waits up to 10 seconds for them before closing the database. Anything still unfinished is logged at ERROR and counted in `events_not_stored`.

A killed process gets no such wait. Each event and its session row are one transaction, so a kill leaves whole rows: writes that committed stay, the rest are gone. Sending the same `event_id` values again completes the set without duplicates.

## A loop that threads cannot wake (Windows)

Results from threads, such as every aiosqlite call, reach the event loop through its self-pipe. On Windows that pipe is a loopback TCP connection, and asyncio ignores errors writing to it. If the connection is reset, those results wait in the ready queue until something else wakes the loop.

This was seen once. A test run hung for good with 15 finished results queued, the writer end of the pipe `CLOSED`, and `send` failing with `WSAECONNRESET` (10054). A Proton VPN disconnect and Windows Filtering Platform transactions owned by its service match the timing, but that link is unproven: the VPN was never toggled on purpose. Windows documents that [a filter change re-authorizes existing flows](https://learn.microsoft.com/en-us/windows/win32/fwp/ale-re-authorization), and [python/cpython#156333](https://github.com/python/cpython/issues/156333) reports self-pipes dying when a WFP driver re-applied its filters, though with a different symptom. [python/cpython#156054](https://github.com/python/cpython/issues/156054) covers the swallowed error.

The API keeps a 50 ms timer running, so the loop never sleeps without a timer and a lost pipe costs latency, not a hang. Its idle cost was too small to measure reliably: 0% to 0.16% of one core over 10 seconds, where 0.16% is one tick of the Windows CPU clock (see [`benchmarks/results/persistence/`](../benchmarks/results/persistence/)). Writes on a loop in this state are slower, and nothing reports that it happened. The timer does not help the other failure in #156333, where the reader sees EOF and the loop spins at 100% CPU, and it covers the API process only, from after the database connects.

To reproduce it, replace `loop._write_to_self` with a no-op, as `test_a_burst_finishes_when_threads_can_no_longer_wake_the_loop` does. Remove the timer and that test fails.

## Recovery

1. `/health` shows `database.status: degraded` or `unavailable`.
2. Restore the database, or fix the network or credentials. A running API resumes writing on its own. An API that refused to start needs a restart once the database is reachable.
3. Writes answered 503 during the outage were not stored. Clients should retry them: the event ingestion route is safe to retry because events carry their own `event_id`.

## Request and payload limits (TO-010)

| Limit | Default | Setting | Rejection |
|---|---|---|---|
| HTTP request body | 10 MiB | `AGENTWATCH_MAX_BODY_BYTES` | **413**, decided from `Content-Length` before the body is read; a chunked body is cut off as soon as it passes the limit |
| observations per v3 batch | 5000 | `AGENTWATCH_MAX_BATCH_ITEMS` | 422 |
| JSON nesting depth (v3 payloads; v1 `metadata` and tool arguments) | 64 | `AGENTWATCH_MAX_JSON_DEPTH` | 422 (v3) / 413 (v1) |
| single string | 4 MiB | `AGENTWATCH_MAX_STRING_BYTES` | 422 (v3) / 413 (v1) |
| WebSocket message | 1 MiB | `AGENTWATCH_WS_MAX_BYTES` (`agentwatch server start`; `--ws-max-size` in the Docker image) | the connection is closed by uvicorn |
| policy dry-run input | see [`TENANCY.md`](TENANCY.md) | | 422 |

The structural checks are iterative, so a pathologically deep document is rejected with a 4xx and never exhausts the recursion limit. Payloads larger than 64 KB are stored in the blob store, and only the limits above bound them.
