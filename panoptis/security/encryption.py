"""
API Key Encryption Module

Provides secure storage and management of API keys using AES-256-GCM encryption.
Keys are encrypted at rest and decrypted only when needed.
"""

from __future__ import annotations

import hashlib
import logging
import os
from base64 import b64decode, b64encode
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2

logger = logging.getLogger(__name__)


class APIKeyEncryption:
    """Secure API key encryption and decryption using AES-256-GCM."""

    def __init__(self, master_key: str | None = None) -> None:
        """Initialize encryption with master key from environment."""
        self.master_key = master_key or os.getenv("API_KEY_ENCRYPTION_KEY")
        if not self.master_key:
            raise ValueError("API_KEY_ENCRYPTION_KEY not set in environment")

        self._encryption_key = self._derive_key(self.master_key)

    @staticmethod
    def _derive_key(master_key: str, salt: bytes = b"panoptis-api") -> bytes:
        """Derive 32-byte encryption key from master key using PBKDF2."""
        kdf = PBKDF2(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        return kdf.derive(master_key.encode())

    def encrypt_key(self, api_key: str) -> tuple[str, str]:
        """
        Encrypt API key using AES-256-GCM.

        Returns:
            (encrypted_key_b64, nonce_b64): Encrypted key and nonce, both base64-encoded
        """
        nonce = os.urandom(12)
        cipher = AESGCM(self._encryption_key)
        ciphertext = cipher.encrypt(nonce, api_key.encode(), None)

        return (
            b64encode(ciphertext).decode(),
            b64encode(nonce).decode(),
        )

    def decrypt_key(self, encrypted_key: str, nonce: str) -> str:
        """
        Decrypt API key using AES-256-GCM.

        Args:
            encrypted_key: Base64-encoded encrypted key
            nonce: Base64-encoded nonce

        Returns:
            Decrypted API key
        """
        try:
            ciphertext = b64decode(encrypted_key)
            nonce_bytes = b64decode(nonce)

            cipher = AESGCM(self._encryption_key)
            plaintext = cipher.decrypt(nonce_bytes, ciphertext, None)
            return plaintext.decode()
        except Exception as e:
            logger.error("Failed to decrypt API key: %s", str(e))
            raise ValueError("Decryption failed") from e

    @staticmethod
    def hash_key(api_key: str) -> str:
        """
        Hash API key for database lookup without storing plaintext.

        Args:
            api_key: Plain API key

        Returns:
            SHA-256 hash of the key, hex-encoded
        """
        return hashlib.sha256(api_key.encode()).hexdigest()


def rotate_key(
    db_session: Any,
    agent_id: str,
    new_key: str,
    rotated_by: str = "system",
    reason: str | None = None,
) -> dict[str, Any]:
    """
    Rotate an agent's API key.

    Args:
        agent_id: ID of the agent
        new_key: New API key
        rotated_by: User or system that initiated rotation
        reason: Reason for rotation (e.g., "periodic", "security_incident")

    Returns:
        Rotation audit entry with timestamps and hashes
    """
    if not db_session:
        raise RuntimeError("Database session required for key rotation")

    from panoptis.security.key_storage import EncryptedAPIKey, KeyRotationAudit

    # Get current key to retrieve old key hash
    current_key = (
        db_session.query(EncryptedAPIKey).filter(EncryptedAPIKey.agent_id == agent_id).first()
    )

    old_key_hash = current_key.key_hash if current_key else None

    # Encrypt new key
    encryption = APIKeyEncryption()
    encrypted_key, nonce = encryption.encrypt_key(new_key)
    new_key_hash = APIKeyEncryption.hash_key(new_key)

    # Update or create key record
    if current_key:
        current_key.encrypted_key = encrypted_key
        current_key.nonce = nonce
        current_key.key_hash = new_key_hash
        current_key.rotated_at = datetime.now(UTC)
    else:
        current_key = EncryptedAPIKey(
            agent_id=agent_id,
            key_hash=new_key_hash,
            encrypted_key=encrypted_key,
            nonce=nonce,
            created_at=datetime.now(UTC),
        )
        db_session.add(current_key)

    # Create audit entry
    rotation_entry = KeyRotationAudit(
        rotation_id=str(uuid4()),
        agent_id=agent_id,
        old_key_hash=old_key_hash,
        new_key_hash=new_key_hash,
        rotated_by=rotated_by,
        rotated_at=datetime.now(UTC),
        reason=reason,
    )
    db_session.add(rotation_entry)
    db_session.commit()

    return {
        "rotation_id": rotation_entry.rotation_id,
        "agent_id": agent_id,
        "old_key_hash": old_key_hash,
        "new_key_hash": new_key_hash,
        "rotated_at": rotation_entry.rotated_at.isoformat(),
        "rotated_by": rotated_by,
        "reason": reason,
    }


def get_rotation_history(
    db_session: Any,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Get rotation history for an agent."""
    if not db_session:
        return []

    from panoptis.security.key_storage import KeyRotationAudit

    rotations = (
        db_session.query(KeyRotationAudit)
        .filter(KeyRotationAudit.agent_id == agent_id)
        .order_by(KeyRotationAudit.rotated_at.desc())
        .all()
    )

    return [
        {
            "rotation_id": r.rotation_id,
            "old_key_hash": r.old_key_hash,
            "new_key_hash": r.new_key_hash,
            "rotated_at": r.rotated_at.isoformat(),
            "rotated_by": r.rotated_by,
            "reason": r.reason,
        }
        for r in rotations
    ]
