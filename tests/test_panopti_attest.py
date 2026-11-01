"""Panopti attestation seals (TO-005): signing, verification, tampering, rotation."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import secrets

import pytest

from panopti.attest import AttestationEngine, PanoptiAttestationSeal
from panopti.attest_keys import AttestationKeyError, Keyring, keyring_from_env

ROOT = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"


def strong_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def ring(**keys: bytes) -> Keyring:
    r = Keyring()
    for kid, key in keys.items():
        r.add(kid, key)
    return r


def test_seal_generation_and_verification() -> None:
    key = secrets.token_bytes(32)
    seal = AttestationEngine.create_seal(
        merkle_root=ROOT, tool_count=5, confidence_floor=0.88, secret_key=key
    )
    assert seal.seal_id.startswith("seal_") and seal.version == "2" and seal.key_id == "explicit"
    assert (seal.policy_verdict, seal.loop_status, seal.tool_count, seal.confidence_floor) == (
        "COMPLIANT",
        "CLEAN",
        5,
        0.88,
    )
    assert AttestationEngine.verify_seal(seal, secret_key=key) is True


def test_no_default_key_exists():
    with pytest.raises(AttestationKeyError, match="no attestation signing key"):
        AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1)


@pytest.mark.parametrize(
    "unsafe", ["panopti-seal-default-key", "panopti-demo-hmac-key", "my-audit-secret"]
)
def test_public_or_weak_keys_cannot_sign_or_verify(unsafe):
    with pytest.raises(AttestationKeyError):
        AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1, secret_key=unsafe)
    forged = PanoptiAttestationSeal(
        "seal_x",
        ROOT,
        "COMPLIANT",
        1.0,
        "CLEAN",
        1,
        "2026-01-01T00:00:00+00:00",
        "",
        "explicit",
        "2",
    )
    forged.signature = hmac.new(unsafe.encode(), b"anything", hashlib.sha256).hexdigest()
    assert AttestationEngine.verify_seal(forged, secret_key=unsafe) is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_verdict", "VIOLATION"),
        ("merkle_root", "0" * 64),
        ("tool_count", 99),
        ("confidence_floor", 0.1),
        ("loop_status", "LOOP_DETECTED"),
        ("timestamp_iso", "2000-01-01T00:00:00+00:00"),
        ("seal_id", "seal_other"),
    ],
)
def test_any_modified_field_fails_verification(field, value):
    r = ring(k1=secrets.token_bytes(32))
    seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=3, keyring=r)
    assert (
        AttestationEngine.verify_seal(dataclasses.replace(seal, **{field: value}), keyring=r)
        is False
    )


def test_modified_signature_and_wrong_key_fail():
    r = ring(k1=secrets.token_bytes(32))
    seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=3, keyring=r)
    flipped = seal.signature[:-1] + ("0" if seal.signature[-1] != "0" else "1")
    assert (
        AttestationEngine.verify_seal(dataclasses.replace(seal, signature=flipped), keyring=r)
        is False
    )
    assert AttestationEngine.verify_seal(seal, keyring=ring(k1=secrets.token_bytes(32))) is False


def test_key_id_cannot_be_swapped():
    r = ring(k1=secrets.token_bytes(32), k2=secrets.token_bytes(32))
    seal = AttestationEngine.create_seal(
        merkle_root=ROOT, tool_count=3, keyring=r
    )  # signed with k2 (active)
    assert AttestationEngine.verify_seal(seal, keyring=r) is True
    assert AttestationEngine.verify_seal(dataclasses.replace(seal, key_id="k1"), keyring=r) is False


def test_rotation_old_seals_still_verify_new_seals_use_new_key():
    k_old, k_new = secrets.token_bytes(32), secrets.token_bytes(32)
    r = ring(old=k_old)
    old_seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1, keyring=r)
    r.add("new", k_new)  # rotate: new active key
    new_seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=2, keyring=r)
    assert (old_seal.key_id, new_seal.key_id) == ("old", "new")
    assert AttestationEngine.verify_seal(old_seal, keyring=r) and AttestationEngine.verify_seal(
        new_seal, keyring=r
    )


def test_revoked_and_unknown_keys_fail_closed():
    k1 = secrets.token_bytes(32)
    r = ring(k1=k1)
    seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1, keyring=r)
    revoked = Keyring(keys={"k1": k1}, active_kid="k1", revoked=frozenset({"k1"}))
    assert AttestationEngine.verify_seal(seal, keyring=revoked) is False
    assert (
        AttestationEngine.verify_seal(dataclasses.replace(seal, key_id="ghost"), keyring=r) is False
    )
    with pytest.raises(AttestationKeyError, match="revoked"):
        AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1, keyring=revoked)


def test_keyring_from_environment(monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ATTEST_KEYS", f"prod-2026={strong_key()}")
    seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=4)
    assert seal.key_id == "prod-2026"
    assert AttestationEngine.verify_seal(seal) is True
    monkeypatch.setenv("AGENTWATCH_ATTEST_REVOKED", "prod-2026")
    assert AttestationEngine.verify_seal(seal) is False


def test_legacy_v1_seals_need_explicit_opt_in_and_a_real_key():
    key = secrets.token_bytes(32)
    legacy = PanoptiAttestationSeal(
        "seal_old", ROOT, "COMPLIANT", 1.0, "CLEAN", 2, "2026-01-01T00:00:00+00:00", ""
    )
    legacy.signature = hmac.new(
        key, AttestationEngine._v1_payload(legacy).encode(), hashlib.sha256
    ).hexdigest()
    assert AttestationEngine.verify_seal(legacy, secret_key=key) is False
    assert AttestationEngine.verify_seal(legacy, secret_key=key, allow_legacy=True) is True
    assert AttestationEngine.verify_seal(legacy, keyring=ring(k=key), allow_legacy=True) is False


def test_json_roundtrip_keeps_version_and_key_id():
    r = ring(k1=secrets.token_bytes(32))
    seal = AttestationEngine.create_seal(
        merkle_root=ROOT, tool_count=10, confidence_floor=0.92, keyring=r
    )
    back = PanoptiAttestationSeal.from_json(seal.to_json())
    assert back == seal and AttestationEngine.verify_seal(back, keyring=r)


def test_dev_key_from_env_round_trips_in_process(monkeypatch):
    monkeypatch.setenv("AGENTWATCH_ATTEST_DEV_KEY", "1")
    r = keyring_from_env()
    seal = AttestationEngine.create_seal(merkle_root=ROOT, tool_count=1, keyring=r)
    assert AttestationEngine.verify_seal(seal, keyring=r)
    assert not AttestationEngine.verify_seal(seal, keyring=keyring_from_env()), (
        "another process's dev key must not verify"
    )


def _run_verifier(*args: str, env: dict | None = None) -> tuple[int, str]:
    import os
    import subprocess
    import sys

    p = subprocess.run(  # noqa: S603 - fixed script, test-controlled arguments
        [sys.executable, "scripts/verify_seal.py", *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    return p.returncode, p.stdout + p.stderr


def test_independent_verifier_agrees_with_the_engine(tmp_path):
    key = secrets.token_bytes(32)
    r = ring(audit=key)
    seal = AttestationEngine.create_seal(
        merkle_root=ROOT, tool_count=7, confidence_floor=0.875, keyring=r
    )
    seal_file, key_file = tmp_path / "seal.json", tmp_path / "key.b64"
    seal_file.write_text(seal.to_json(), encoding="utf-8")
    key_file.write_text(base64.b64encode(key).decode(), encoding="utf-8")
    assert _run_verifier(str(seal_file), "--key-file", str(key_file))[0] == 0
    code, out = _run_verifier(
        str(seal_file), env={"AGENTWATCH_ATTEST_KEYS": f"audit={base64.b64encode(key).decode()}"}
    )
    assert code == 0 and "VALID" in out

    tampered = dataclasses.replace(seal, policy_verdict="VIOLATION")
    seal_file.write_text(tampered.to_json(), encoding="utf-8")
    assert _run_verifier(str(seal_file), "--key-file", str(key_file))[0] == 1
    other = tmp_path / "other.b64"
    other.write_text(base64.b64encode(secrets.token_bytes(32)).decode(), encoding="utf-8")
    seal_file.write_text(seal.to_json(), encoding="utf-8")
    assert _run_verifier(str(seal_file), "--key-file", str(other))[0] == 1
    assert _run_verifier(str(seal_file))[0] == 2  # no key configured
