# Witness attestation: Merkle proofs, audit tokens, hash commitments, crypto-shredding

This describes four pieces in `panoptis/evidence/` that let a third party check a witness
without access to the evidence store, and let a subject's data be erased without breaking those
checks.

| Module | What it does | Dependencies |
|---|---|---|
| `merkle.py` | Domain-separated Merkle tree, inclusion proofs, verifier | standard library |
| `attestation.py` | Signed audit token, hash commitments, rule check | standard library (HMAC-SHA256); optional Ed25519 needs `cryptography` |
| `shredding.py` | Per-subject AES-256-GCM encryption, `erase(subject)` | `cryptography` (extra `crypto`) |
| `crypto.py` | Existing AES-GCM envelope helpers, now with `require_cryptography()` | as above |

The core (everything except AES-GCM and the optional Ed25519 signer) uses only `hashlib`, `hmac`,
`json` and `secrets`. `cryptography` is imported lazily. If it is missing, `ShreddingVault()` and
`Ed25519Signer` raise `CryptoImportError`, which is an `ImportError` and a `CryptoUnavailableError`,
with the message `pip install 'panoptis[crypto]'`. No cipher is implemented in this repo.

## 1. Merkle tree and inclusion proofs

The construction follows RFC 6962 section 2.1 (tree) and RFC 9162 section 2.1.3.2 (proof check).

```
leaf node      = SHA-256( 0x00 || leaf_hash )            leaf_hash is a 32-byte digest
interior node  = SHA-256( 0x01 || left || right )
empty tree     = SHA-256( "" )
```

- The `0x00` / `0x01` prefixes put leaves and interior nodes in different hash domains. An interior
  node cannot be passed off as a leaf, because as a leaf it would be hashed again with `0x00`.
  Leaf hashes must be 32-byte digests (64 lowercase hex characters), so a 64-byte `left || right`
  cannot be presented as one either.
- An odd node at the end of a level is promoted unchanged. It is not paired with a copy of itself, so
  `[a, b, c]` and `[a, b, c, c]` have different roots.

Proof format (JSON):

```json
{"leaf_index": 4, "tree_size": 9, "path": ["<hex sibling>", "..."]}
```

`path` runs from the leaf level to the top. A level where the node had no sibling adds nothing.
To verify, an auditor needs three things: the leaf hash, this proof and the root. The left or right
position of each sibling is derived from `leaf_index` and `tree_size` while walking the path
(RFC 9162), so reordering the path, moving the index, or truncating the path changes the result.

```python
from panoptis.evidence.merkle import MerkleProof, MerkleTree, verify_inclusion

tree = MerkleTree(leaf_hashes)                  # sealed segment
proof = tree.prove(4)
verify_inclusion(leaf_hashes[4], proof, tree.root)   # True
```

`verify_inclusion` returns `False` for malformed input instead of raising. The test suite also
checks proofs with a second verifier that uses only `hashlib` and `json` and a different
derivation (the RFC 6962 recursion), to show the format is enough for an independent
implementation.

## 2. Audit token

One token proves "this witness leaf is in the sealed segment with this root, as of this time",
signed by the issuer.

```json
{
  "version": "panoptis-audit-token/1",
  "segment_id": "seg-0007",
  "segment_root": "<hex>",
  "leaf_hash": "<hex>",
  "proof": {"leaf_index": 4, "tree_size": 9, "path": ["..."]},
  "timestamp": "2026-10-27T09:30:00+00:00",
  "alg": "HMAC-SHA256",
  "key_id": "audit-key-1",
  "fields": {"<name>": "<commitment hex>"},
  "signature": "<hex>"
}
```

`fields` is optional (section 3). The signed bytes are the version line, a newline, and the
canonical JSON (`panoptis.evidence.canonical.canonical_json`: sorted keys, no spaces) of every
field except `signature`. So the signature covers the root, leaf, proof (including `tree_size`),
timestamp, algorithm, key id and field commitments.

`verify_token(token, verifier, expected_root=None)` checks, in this order: format version,
that `alg` and `key_id` match the verifier (the token does not choose how it is verified),
the signature, the timestamp format, the optional pinned root, the Merkle proof, and that the
field commitments hash to the leaf. It returns a result that is truthy on success and carries a
reason otherwise.

### Signatures

- **HMAC-SHA256** (default, standard library): the scheme of `panopti/attest.py`, an HMAC with
  SHA-256 over the signed text, hex encoded, compared in constant time. Keys must be at least 16
  bytes and are never defaulted. HMAC is symmetric: **whoever can verify can also forge.** Use it
  where issuer and verifier trust each other (internal audits, tests).
- **Ed25519** (optional, needs `cryptography`): the auditor holds only a public key
  (`Ed25519Verifier`), so verifying does not let them issue tokens.

## 3. Hash commitments and rule checks

A commitment to a value is `SHA-256( "panoptis/commitment/v1\0" || nonce || canonical_json(value) )`
with a random 32-byte nonce.

- **Hiding:** without the nonce the digest does not reveal the value, even for guessable values such
  as `true`, because of the 256-bit nonce.
- **Binding:** opening a commitment to a different value needs a SHA-256 collision.

A record is committed field by field (`CommittedRecord`). The record digest is the SHA-256 of the
field names and their commitments, and that digest is the witness leaf hash that goes into the Merkle
tree. To show a rule such as `human_approval == true`, the owner opens only that field:

```python
record = CommittedRecord({"human_approval": True, "prompt": "...", "parameters": {...}})
leaves[3] = record.digest
token = issue_token(MerkleTree(leaves), 3, segment_id="seg-9", signer=signer, record=record)

verify_rule(token, signer, record.disclose("human_approval"), {"human_approval": True})
```

The auditor learns that the signed segment contains a record whose `human_approval` field is
exactly `true`, and nothing about the other fields' values. Values are compared in canonical JSON
form, so `true` does not satisfy a rule that requires `1`.

**These are hash commitments with selective disclosure. They are not zero-knowledge proofs.** There is
no proof of a statement about a hidden value: the auditor sees the opened fields in full, and the rule
must be a set of required field values. A range proof or "approver is in group X" would need a real
ZK system and is out of scope.

## 4. Crypto-shredding

```
envelope  = "enc:v1:" + key_id + ":" + base64(nonce || AES-256-GCM(key_subject, payload))
leaf_hash = SHA-256( envelope )          # over the ciphertext
```

- Each subject gets a random 256-bit key from a key store. The key id is random and carries no
  subject information; it is bound into the ciphertext as GCM associated data, so an envelope
  cannot be relabelled to another subject's key.
- Because the leaf hash is computed over the envelope, it never depended on the plaintext or the key.
- `ShreddingVault.erase(subject)` destroys the key. Afterwards `decrypt` raises
  `SubjectErasedError`. The envelope, its leaf hash, the segment root and every proof are unchanged, so
  `verify_inclusion` and previously issued audit tokens still verify. New encryption for an erased
  subject is refused (it does not silently start a new key).
- `InMemoryKeyStore` is a reference implementation. Plug in a KMS or HSM by implementing the three
  methods of the `KeyStore` protocol.

This is the same envelope format and the same idea as ADR-0011 and `Store(encrypt_payloads=True)`
(`panoptis/storage`). This module is the store-independent building block: it works on plain
payloads and a Merkle tree, without a database.

## 5. Threat model

What an auditor can rely on, assuming SHA-256 is collision resistant, the signing key was not
compromised, and the auditor got the verification key or pinned root over a trusted channel:

- A leaf that verifies against a signed root was in the tree the issuer signed. Changing the leaf,
  its position, a sibling or the root makes verification fail.
- Opening a field commitment to a value other than the committed one is not possible.
- After `erase(subject)`, the stored evidence does not contain that subject's plaintext and the
  evidence still verifies.

## 6. What this does not guarantee

- **No trusted time.** The timestamp is the issuer's claim, covered by its signature. There is no
  RFC 3161 time-stamping authority or external anchor. Someone holding the signing key could issue a
  token with any time. Anchoring roots in an external log is a possible follow-up.
- **No completeness.** A token proves that a leaf is in a signed segment. It does not prove the segment
  holds every observation, or that the issuer sealed all segments. Chaining segments is done by
  `panoptis/evidence/segments.py` and is separate from this module.
- **The root does not authenticate the exact tree size.** As in RFC 6962, sizes with the same path
  shape give the same root. The size in a token is protected by the signature, not by the root.
  A bare proof with a root from elsewhere pins the leaf position, not the segment size.
- **HMAC tokens are forgeable by verifiers.** See section 2.
- **Key store is in memory.** `InMemoryKeyStore` loses keys when the process exits and holds them
  in process memory. Python cannot guarantee that every copy of a key is wiped (it overwrites the
  stored copy, but copies handed to the cipher or made by the interpreter may linger). Use a KMS or
  HSM in production.
- **Erasure covers what was encrypted with the destroyed key.** Plaintext that left the vault before
  erasure (logs, exports, caches, backups, a process that decrypted it) is not erased. Subject ids,
  field names in commitments and any metadata kept outside the envelope are not encrypted.
  Whether a ciphertext whose key is destroyed counts as erased personal data is a legal question,
  not a cryptographic one.
- **Commitment limits.** Field names, the number of fields and the existence of the record are
  visible. A commitment to a low-entropy value is safe only while its nonce stays secret.
- **Existing `segments.py` is unchanged.** It uses its own tree construction (`leaf|` and
  `node|` text prefixes, last node duplicated on odd levels). The tree here is separate and is not yet
  wired into the segment sealer or the store.
