# ADR-0011: Redact at the edge with manifests; reconcile erasure with immutability via crypto-shredding

- Status: Proposed
- Date: 2026-09-25

## Context

Payloads (prompts, completions, documents) often contain personal data. v0.2 redacts in place after capture, which races with persistence (D2). It erases by deleting rows. An immutable evidence layer cannot delete rows without breaking its integrity chain, but legal erasure obligations remain.

## Decision

**Edge redaction:**

- `security/redaction.py` runs in the sensor (when configured) or at ingest, *before* the append.
- The observation stores the redacted payload plus a `redaction_manifest`: the fields and spans redacted, the detector name and version, and counts.
- The original is never stored.
- Redaction is never applied to stored evidence after the fact.

**Encryption and erasure:**

- Payloads and blobs are encrypted with per-tenant data keys. Optionally, when a subject key is declared, per-subject keys are used instead (`security/encryption.py`, `key_storage.py`).
- Segment Merkle trees hash the *ciphertext*.
- **Erasure = destroy the key (crypto-shredding).** The payload becomes unreadable, and the integrity chain still verifies.
- Derived records that embed payload fragments (artifact previews) are purged and re-derived from the now-unreadable evidence, so they fall back to hashes only.

Tenancy uses HMAC-keyed artifact ids per tenant, which prevents cross-tenant content-existence oracles.

## Consequences

- Key management becomes critical infrastructure. Loss of a tenant key means loss of that tenant's payloads. Backup procedures are documented in Phase 1.
- Full-text search over payloads requires decryption at query time, or a separately governed index (later, opt-in).

## As built (2026-09-26)

- **Opt-in encryption.** Enabled with `Store(..., encrypt_payloads=True)` or `AGENTWATCH_ENCRYPT_PAYLOADS=1`; requires `cryptography`.
  - Each (tenant, subject) pair gets its own AES-256-GCM key in `aw3_data_keys`.
  - The subject is the declared id `subject_id`, e.g. `aw.run(..., subject_id=...)`. Observations without one use the tenant default key.
- **What is hashed.** Stored payloads are ciphertext (`enc:v1:<key_id>:…`), and the segment chain hashes the ciphertext. The dedup key is an HMAC under the subject key, so no plaintext hash survives erasure.
- **Erasure.** `Engine.erase_subject` / `panoptis evidence erase-subject` / `POST /api/v3/evidence/erase`:
  1. destroys the key;
  2. deletes every derived row of the tenant (all interpretations, and all artifacts), plus experiment records about the affected runs;
  3. logs the erasure in `aw3_erasures`;
  4. rebuilds. Erased observations become `payload_erased` diagnostics.

  The evidence chain still verifies afterwards. New payloads for an erased subject are refused.
- **Limitations:**
  - Plaintext that reached systems outside AgentWatch (logs, exports, backups) is out of scope.
  - Declared ids themselves (for example `subject_id`) are not encrypted.
  - A process that decrypted a payload before erasure may still hold it in memory.
