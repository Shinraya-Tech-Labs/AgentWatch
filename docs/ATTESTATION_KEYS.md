# Attestation signing keys

Panopti attestation seals are HMAC-SHA256 signatures over a canonical JSON document of the seal. Anyone holding the key can create a valid seal, so the key is the whole security of the scheme.

## There is no default key

Earlier versions signed with `panopti-seal-default-key`, a key published in the source, so any seal "verified". That key and the old demo key are now rejected, along with any key shorter than 32 bytes. With no key configured, creating a seal raises `AttestationKeyError`, and `panopti attest verify` exits with code 2.

## Generate a key

```bash
python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

## Configure

| Variable | Meaning |
|---|---|
| `AGENTWATCH_ATTEST_KEYS` | `kid=<base64 or hex key>` entries, separated by commas or newlines |
| `AGENTWATCH_ATTEST_ACTIVE_KID` | the key id new seals are signed with (default: the last one listed) |
| `AGENTWATCH_ATTEST_REVOKED` | key ids that must no longer verify (comma separated) |
| `AGENTWATCH_REQUIRE_ATTESTATION=1` | in production or cloud mode, refuse to start without keys |
| `AGENTWATCH_ATTEST_DEV_KEY=1` | development only: an ephemeral random key for this process. Refused in production and cloud mode |

Load the keys from your secret manager into the environment (Kubernetes secret, Docker secret, Vault agent); never commit them. Key material is never logged: startup logs list only the key ids, the active id and the revoked ids.

In production or cloud mode the API server refuses to start when keys are configured but unsafe, or required but missing.

## Seal format (version 2)

Every field is signed, including `version` and `key_id`, so a seal cannot be re-attributed to another key. Verification:

- looks up `key_id` in the keyring; an **unknown or revoked id fails**;
- fails on any modified field or signature;
- verifies version 1 seals (which have no key id) only with `allow_legacy=True` and an explicit, non-public key.

## Rotate

1. Generate the new key and add it: `AGENTWATCH_ATTEST_KEYS=old=<key>,new=<key>`.
2. Set `AGENTWATCH_ATTEST_ACTIVE_KID=new` and restart. New seals use `new`; old seals still verify with `old`.
3. Keep `old` for as long as its seals must remain verifiable.

## Back up

Back up every key whose seals you may need to verify, in the same secret manager as the rest of your secrets, with access logging. A lost key makes its seals unverifiable. It does not make them forgeable.

## Incident response (key exposed)

1. Add the exposed key id to `AGENTWATCH_ATTEST_REVOKED`, activate a new key, and restart. Seals signed with the exposed key now fail verification.
2. Treat every seal signed with that key as untrusted, and re-issue the seals you still need under the new key from the underlying evidence: the v3 Merkle chain is verified separately (`agentwatch evidence verify`).
3. Rotate wherever the key was stored, and review the secret manager's access log.

## Independent verification

```bash
python scripts/verify_seal.py seal.json --key-file key.b64
```

The script uses only the standard library and shares no code with the engine. Exit codes: 0 = valid, 1 = invalid, 2 = no key or unreadable input.
