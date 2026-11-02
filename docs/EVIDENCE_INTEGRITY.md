# Evidence integrity

Every protection decision, approval, execution, output inspection, control action and discovery inventory is a record in the append-only evidence store. Records are sealed into hash-chained Merkle segments, and an independent verifier accepts valid evidence and rejects tampered or incomplete evidence.

Code: `panoptis/storage/store.py` (`seal`, `verify`, `export_bundle`, `inclusion_proof`), `panoptis/evidence/segments.py`, `panoptis/protect/ledger.py`, `panoptis/evidence/verifier.py` (standalone). Tests: `tests/protect/test_evidence_integrity.py`.

## What protects the records

| Layer | What it does |
|---|---|
| **Append-only database guard** | Triggers (SQLite and PostgreSQL) refuse `UPDATE`/`DELETE` on observations, segments and segment versions. The only exceptions are assigning an unsealed record to a segment and an authorized retention purge. Every envelope column is immutable. |
| **Payload hash** | `payload_sha256` over the stored payload, which is the ciphertext when payloads are encrypted. |
| **Leaf (version 2)** | SHA-256 over the canonical JSON of the whole stored envelope: id, tenant, kind, sensor, sequence, idempotency key, observed and received time, clock, content type, payload hash, declared ids (agent, session, call, verdict, …), redaction manifest and sampling. |
| **Segment** | Merkle root over the leaves (ordered by id). The segment hash binds tenant, sequence, root, previous segment hash, count and leaf version, so a segment cannot be downgraded to the weaker version-1 leaf. |
| **Chain** | Each segment names the previous segment's hash, starting from genesis. Sequence numbers have no gaps. |
| **Anchor** | The head (sequence and hash of the last segment) seen by the last successful verification is kept **outside the store**, in `AGENTWATCH_EVIDENCE_ANCHORS` (default `~/.agentwatch/anchors/`). A later verification fails if that head is no longer in the chain. A head can also be given explicitly (`--expect-head SEQ:HASH`), for example from an earlier export or a signed audit token. |
| **Index check** | The declared-id search index must match each record's hash-covered declared ids. |

Segments sealed before leaf version 2 keep verifying with version 1. New segments chain onto them with version 2.

## Sealing

The protect ledger seals automatically: every 200 records or 30 seconds per tenant (`ProtectLedger(seal_every=, seal_interval_s=)`). `verify`, `evidence seal` and `evidence export` also seal first.

**Until a record is sealed it is not integrity-protected.** Verification reports the number of unsealed records as a warning. It also warns when an unsealed record is older than the last sealed one, which is what a forged insertion looks like (it can also be a late write).

## Verifying

```bash
panoptis protect evidence verify [--tenant acme] [--expect-head 12:ab34…] [--no-anchor]
panoptis protect evidence export -o bundle.json [--tenant acme]
panoptis protect evidence verify-bundle bundle.json [--expect-head 12:ab34…]
panoptis protect evidence proof <obs_id> -o proof.json
python panoptis/evidence/verifier.py bundle.json            # the same check, standalone
python panoptis/evidence/verifier.py --proof proof.json
```

**`verifier.py` is independent.** It uses only the Python standard library and imports nothing from AgentWatch, so it can be copied and run anywhere (the tests run it with `python -I` from an empty directory). It recomputes:

- every payload hash;
- every leaf from the exported envelope;
- every Merkle root, count and first/last id;
- every segment hash and the whole chain;
- for proofs: the record's leaf, its path to the root and the segment hash.

Exit status 0 means valid, 1 means tampered, incomplete or malformed. `evidence verify` exits 2 when verification fails.

## What is detected

Tested by tampering with the database file directly, with the triggers dropped:

| Tampering | Store `verify` | Standalone verifier |
|---|---|---|
| a payload changed (with or without its hash) | ✓ | ✓ |
| kind, declared ids (verdict, agent, session), timestamp or sensor changed | ✓ | ✓ |
| a record deleted, records swapped (reordered), a record moved to another segment | ✓ | ✓ |
| a middle segment removed, a segment hash changed | ✓ | ✓ |
| the leaf version downgraded | ✓ | ✓ |
| the declared-id index changed | ✓ | n/a (not exported; the bundle carries the covered declared ids) |
| the newest segments cut off (truncation) | ✓ with the anchor or an expected head | ✓ with `--expect-head` |
| a segment rewritten below a known head | ✓ with an expected head | ✓ with `--expect-head` |
| a forged unsealed record | reported as a warning (not yet covered) | not exported |

Without an anchor or a known head, a chain cut back to an earlier segment is internally consistent and cannot be detected. Keep the anchor, export bundles regularly, or issue signed audit tokens (`panoptis/evidence/attestation.py`).

## What the records contain

- **Tenant and identity.** Every record names its tenant. Decisions carry the full action: tenant, agent, session, call id, tool, arguments after redaction, trace and parent call. They also carry the policy version, the threat-rules version, timings and the verdict.
- **Outcomes are distinguishable:**
  - decisions are `allow`, `block`, `escalate_hitl` or `mutate_and_allow`, with `mode` and `would_block` for observe mode;
  - approval request and decision records show what was escalated;
  - `protect.execution` records the tool actually running;
  - `protect.output` records sanitization: quarantine, redactions, withheld content;
  - `protect.control`, `protect.agent` and `protect.discovery` record the rest.
- **No plaintext secrets.** Secrets (known key formats, credential assignments, private keys, bearer tokens, JWTs) are redacted before storage, with a redaction manifest. A prompt-injection attempt is kept only as a short excerpt in the finding, as attack evidence. The injected text never reaches the recorded result.

## Retention, export and erasure

- **Retention purge** (`evidence purge`) removes a sealed segment's records under a recorded authorization. The segment row and its hashes stay, so the chain still verifies and the purge is visible (`purged_segments`). The standalone verifier accepts purged segments and checks the rest.
- **Tenant-scoped erasure** (crypto-shredding, TO-004) destroys a subject's data key. The ciphertext and its hashes stay, so integrity still verifies, while the content is unrecoverable.
- **Export** writes payloads in their stored form. Encrypted payloads stay encrypted; nothing is decrypted for verification.
