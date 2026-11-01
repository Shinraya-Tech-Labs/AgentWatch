"""TO-005: attestation signing-key configuration."""

from __future__ import annotations

import base64
import secrets

import pytest

from panopti.attest_keys import AttestationKeyError, Keyring, keyring_from_env, parse_keys


def b64(n: int = 32) -> str:
    return base64.b64encode(secrets.token_bytes(n)).decode()


def test_missing_configuration_is_an_error_not_a_default():
    with pytest.raises(AttestationKeyError, match="no attestation signing key"):
        keyring_from_env({})


@pytest.mark.parametrize("public", [b"panopti-seal-default-key", b"panopti-demo-hmac-key"])
def test_known_public_default_keys_are_rejected(public):
    with pytest.raises(AttestationKeyError, match="known public default"):
        Keyring().add("k1", public)
    with pytest.raises(AttestationKeyError):
        parse_keys(f"k1={base64.b64encode(public).decode()}")


def test_weak_keys_are_rejected():
    with pytest.raises(AttestationKeyError, match="at least 32"):
        parse_keys(f"k1={b64(16)}")


@pytest.mark.parametrize("raw", ["k1", "k1=", "bad kid!=" + "A" * 44, "k1=not-base64-@@@"])
def test_malformed_entries_are_rejected(raw):
    with pytest.raises(AttestationKeyError):
        parse_keys(raw)


def test_duplicate_kid_is_rejected():
    k = b64()
    with pytest.raises(AttestationKeyError, match="twice"):
        parse_keys(f"k1={k},k1={k}")


def test_hex_and_base64_keys_and_active_kid():
    hexkey = secrets.token_bytes(32).hex()
    ring = keyring_from_env(
        {
            "AGENTWATCH_ATTEST_KEYS": f"old={b64()}\nnew={hexkey}",
            "AGENTWATCH_ATTEST_ACTIVE_KID": "old",
        }
    )
    assert ring.signing_key()[0] == "old"
    assert (
        keyring_from_env({"AGENTWATCH_ATTEST_KEYS": f"old={b64()},new={hexkey}"}).signing_key()[0]
        == "new"
    )
    with pytest.raises(AttestationKeyError, match="not in"):
        keyring_from_env(
            {"AGENTWATCH_ATTEST_KEYS": f"old={b64()}", "AGENTWATCH_ATTEST_ACTIVE_KID": "missing"}
        )


def test_errors_and_summaries_never_contain_key_material():
    key = b64()
    ring = keyring_from_env({"AGENTWATCH_ATTEST_KEYS": f"k1={key}"})
    assert key not in str(ring.describe())
    with pytest.raises(AttestationKeyError) as exc:
        parse_keys(f"k1={base64.b64encode(b'short-secret-value').decode()}")
    assert "short-secret-value" not in str(exc.value)


def test_dev_key_is_ephemeral_and_refused_in_production():
    a = keyring_from_env({"AGENTWATCH_ATTEST_DEV_KEY": "1"})
    b = keyring_from_env({"AGENTWATCH_ATTEST_DEV_KEY": "1"})
    assert a.ephemeral and a.signing_key()[1] != b.signing_key()[1]
    for prod in ({"AGENTWATCH_ENV": "production"}, {"AGENTWATCH_CLOUD_MODE": "1"}):
        with pytest.raises(AttestationKeyError, match="development-only"):
            keyring_from_env({"AGENTWATCH_ATTEST_DEV_KEY": "1", **prod})
