"""
Secure API Key Storage

Database models and utilities for storing encrypted API keys with audit trails.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.orm import declarative_base

logger = logging.getLogger(__name__)
Base = declarative_base()


class EncryptedAPIKey(Base):
    """Encrypted API key storage model."""

    __tablename__ = "encrypted_api_keys"

    agent_id = Column(String(255), primary_key=True)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    encrypted_key = Column(Text, nullable=False)
    nonce = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(UTC))
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class KeyRotationAudit(Base):
    """Audit trail for API key rotations."""

    __tablename__ = "key_rotation_audit"

    rotation_id = Column(String(36), primary_key=True)
    agent_id = Column(String(255), nullable=False, index=True)
    old_key_hash = Column(String(64), nullable=True)
    new_key_hash = Column(String(64), nullable=False)
    rotated_by = Column(String(255), nullable=False)
    rotated_at = Column(DateTime(timezone=True), default=datetime.now(UTC))
    reason = Column(String(255), nullable=True)


class KeyAccessAudit(Base):
    """Audit trail for API key access."""

    __tablename__ = "key_access_audit"

    access_id = Column(String(36), primary_key=True)
    agent_id = Column(String(255), nullable=False, index=True)
    accessed_by = Column(String(255), nullable=False)
    accessed_at = Column(DateTime(timezone=True), default=datetime.now(UTC))
    access_type = Column(String(50), nullable=False)  # "decrypt", "list", etc
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
