"""Payload encryption for crypto-shredding (ADR-0011).

Each (tenant, subject) pair gets its own 256-bit data key. The subject is taken from the
declared id ``subject_id``; observations without one use the tenant's default key.
Payloads are stored as ``enc:v1:<key_id>:<base64(nonce | ciphertext)>`` (AES-256-GCM, with
the key id as associated data).

Erasing a subject destroys its key. The stored ciphertext, and therefore the evidence hash
chain, is unchanged, but the payload can no longer be read.
"""

from __future__ import annotations

import base64
import os

PREFIX = "enc:v1:"


class CryptoUnavailableError(RuntimeError):
    pass


class CryptoImportError(CryptoUnavailableError, ImportError):
    """The optional ``cryptography`` package is missing.

    Both a ``CryptoUnavailableError`` (existing handlers keep working) and an ``ImportError``.
    """


def require_cryptography(feature: str) -> None:
    """Raise ``CryptoImportError`` with install instructions when ``cryptography`` is absent."""
    try:
        import cryptography  # noqa: F401
    except ImportError as exc:
        raise CryptoImportError(
            f"{feature} requires the 'cryptography' package. "
            "Install it with: pip install 'panoptis[crypto]'"
        ) from exc


def _aesgcm(key: bytes):  # type: ignore[no-untyped-def]
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise CryptoUnavailableError(
            "payload encryption requires the 'cryptography' package (pip install panoptis[crypto])"
        ) from exc
    return AESGCM(key)


def available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:  # pragma: no cover
        return False
    return True


def new_key() -> bytes:
    return os.urandom(32)


def encrypt(plaintext: str, key: bytes, key_id: str) -> str:
    nonce = os.urandom(12)
    ct = _aesgcm(key).encrypt(nonce, plaintext.encode("utf-8"), key_id.encode("utf-8"))
    return f"{PREFIX}{key_id}:{base64.b64encode(nonce + ct).decode('ascii')}"


def is_encrypted(stored: str | None) -> bool:
    return bool(stored) and stored.startswith(PREFIX)  # type: ignore[union-attr]


def key_id_of(stored: str) -> str:
    return stored[len(PREFIX) :].split(":", 1)[0]


def decrypt(stored: str, key: bytes) -> str:
    key_id, _, blob = stored[len(PREFIX) :].partition(":")
    raw = base64.b64decode(blob)
    return _aesgcm(key).decrypt(raw[:12], raw[12:], key_id.encode("utf-8")).decode("utf-8")
