"""
AgentWatch Governance Engine
Policy enforcement, audit logging, capability controls, and RBAC.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    # Session permissions
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_DELETE = "session:delete"

    # Safety
    SAFETY_OVERRIDE = "safety:override"
    SAFETY_POLICY_WRITE = "safety:policy:write"

    # Rollback
    ROLLBACK_TRIGGER = "rollback:trigger"
    CHECKPOINT_CREATE = "checkpoint:create"

    # Plugin
    PLUGIN_INSTALL = "plugin:install"
    PLUGIN_DISABLE = "plugin:disable"

    # Memory
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_DELETE = "memory:delete"

    # Admin
    ADMIN_ALL = "admin:*"


class AuditEventType(str, Enum):
    SAFETY_OVERRIDE = "safety_override"
    POLICY_CHANGE = "policy_change"
    ROLLBACK = "rollback"
    PLUGIN_INSTALL = "plugin_install"
    PLUGIN_DISABLE = "plugin_disable"
    PERMISSION_DENIED = "permission_denied"
    SESSION_DELETE = "session_delete"
    MEMORY_DELETE = "memory_delete"
    CONFIG_CHANGE = "config_change"


@dataclass
class Role:
    name: str
    permissions: set[Permission] = field(default_factory=set)
    description: str = ""

    def has_permission(self, perm: Permission) -> bool:
        if Permission.ADMIN_ALL in self.permissions:
            return True
        return perm in self.permissions


@dataclass
class Principal:
    principal_id: str
    name: str
    roles: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_permission(self, perm: Permission, role_registry: dict[str, Role]) -> bool:
        for role_name in self.roles:
            role = role_registry.get(role_name)
            if role and role.has_permission(perm):
                return True
        return False


@dataclass
class AuditEntry:
    audit_id: str
    timestamp: datetime
    principal_id: str | None
    event_type: AuditEventType
    resource: str
    action: str
    allowed: bool
    session_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "timestamp": self.timestamp.isoformat(),
            "principal_id": self.principal_id,
            "event_type": self.event_type.value,
            "resource": self.resource,
            "action": self.action,
            "allowed": self.allowed,
            "session_id": self.session_id,
            "details": self.details,
        }


# ─────────────────────────────────────────────
# Built-in roles
# ─────────────────────────────────────────────

BUILTIN_ROLES: dict[str, Role] = {
    "viewer": Role(
        name="viewer",
        description="Read-only access to sessions and traces",
        permissions={Permission.SESSION_READ, Permission.MEMORY_READ},
    ),
    "operator": Role(
        name="operator",
        description="Can manage sessions and trigger rollbacks",
        permissions={
            Permission.SESSION_READ,
            Permission.SESSION_CREATE,
            Permission.ROLLBACK_TRIGGER,
            Permission.CHECKPOINT_CREATE,
            Permission.MEMORY_READ,
            Permission.MEMORY_WRITE,
        },
    ),
    "safety_admin": Role(
        name="safety_admin",
        description="Can modify safety policies",
        permissions={
            Permission.SESSION_READ,
            Permission.SAFETY_POLICY_WRITE,
            Permission.SAFETY_OVERRIDE,
        },
    ),
    "plugin_admin": Role(
        name="plugin_admin",
        description="Can install and manage plugins",
        permissions={
            Permission.SESSION_READ,
            Permission.PLUGIN_INSTALL,
            Permission.PLUGIN_DISABLE,
        },
    ),
    "admin": Role(
        name="admin",
        description="Full administrative access",
        permissions={Permission.ADMIN_ALL},
    ),
}


# ─────────────────────────────────────────────
# Governance Engine
# ─────────────────────────────────────────────


class GovernanceEngine:
    """
    Enforces access control and maintains an immutable audit log.
    """

    def __init__(
        self,
        audit_log_path: Path | None = None,
        roles: dict[str, Role] | None = None,
    ):
        self._roles = {**BUILTIN_ROLES, **(roles or {})}
        self._principals: dict[str, Principal] = {}
        self._audit_log: list[AuditEntry] = []
        self._audit_path = audit_log_path
        self._entry_counter = 0

        if self._audit_path:
            self.load_from_disk()

    def load_from_disk(self) -> int:
        """Load persisted audit entries from the JSONL file into memory.

        Returns the number of entries loaded.
        """
        if not self._audit_path or not self._audit_path.exists():
            return 0

        count = 0
        try:
            with open(self._audit_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = AuditEntry(
                            audit_id=data["audit_id"],
                            timestamp=datetime.fromisoformat(data["timestamp"]),
                            principal_id=data.get("principal_id"),
                            event_type=AuditEventType(data["event_type"]),
                            resource=data["resource"],
                            action=data["action"],
                            allowed=data["allowed"],
                            session_id=data.get("session_id"),
                            details=data.get("details", {}),
                        )
                        self._audit_log.append(entry)
                        count += 1
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
                        logger.warning("Skipping corrupt audit entry: %s", exc)
        except OSError as exc:
            logger.warning("Failed to read audit log from %s: %s", self._audit_path, exc)

        logger.info("Loaded %d audit entries from %s", count, self._audit_path)
        return count

    def register_principal(self, principal: Principal) -> None:
        self._principals[principal.principal_id] = principal
        logger.debug("Registered principal: %s (%s)", principal.name, principal.roles)

    def add_role(self, role: Role) -> None:
        self._roles[role.name] = role

    def check_permission(
        self,
        principal_id: str,
        permission: Permission,
        resource: str = "",
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> bool:
        principal = self._principals.get(principal_id)

        if principal is None:
            allowed = False
        else:
            allowed = principal.has_permission(permission, self._roles)

        if not allowed:
            self._audit(
                principal_id=principal_id,
                event_type=AuditEventType.PERMISSION_DENIED,
                resource=resource,
                action=permission.value,
                allowed=False,
                session_id=session_id,
                details=details or {},
            )

        return allowed

    def require_permission(
        self,
        principal_id: str,
        permission: Permission,
        resource: str = "",
    ) -> None:
        if not self.check_permission(principal_id, permission, resource):
            raise PermissionError(
                f"Principal '{principal_id}' lacks permission '{permission.value}' on '{resource}'"
            )

    def record_action(
        self,
        principal_id: str | None,
        event_type: AuditEventType,
        resource: str,
        action: str,
        allowed: bool = True,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        return self._audit(
            principal_id=principal_id,
            event_type=event_type,
            resource=resource,
            action=action,
            allowed=allowed,
            session_id=session_id,
            details=details or {},
        )

    def _audit(
        self,
        principal_id: str | None,
        event_type: AuditEventType,
        resource: str,
        action: str,
        allowed: bool,
        session_id: str | None,
        details: dict[str, Any],
    ) -> AuditEntry:
        import uuid

        self._entry_counter += 1

        entry = AuditEntry(
            audit_id=str(uuid.uuid4()),
            timestamp=datetime.now(UTC),
            principal_id=principal_id,
            event_type=event_type,
            resource=resource,
            action=action,
            allowed=allowed,
            session_id=session_id,
            details=details,
        )
        self._audit_log.append(entry)

        level = logging.WARNING if not allowed else logging.INFO
        logger.log(
            level,
            "AUDIT [%s] %s -> %s on %s: %s",
            entry.event_type.value,
            principal_id or "anonymous",
            action,
            resource,
            "ALLOWED" if allowed else "DENIED",
        )

        if self._audit_path:
            self._flush_entry(entry)

        return entry

    def _flush_entry(self, entry: AuditEntry) -> None:
        if not self._audit_path:
            return
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._audit_path, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    def get_audit_log(
        self,
        limit: int = 100,
        principal_id: str | None = None,
        event_type: AuditEventType | None = None,
        allowed_only: bool | None = None,
    ) -> list[AuditEntry]:
        entries = list(reversed(self._audit_log))
        if principal_id:
            entries = [e for e in entries if e.principal_id == principal_id]
        if event_type:
            entries = [e for e in entries if e.event_type == event_type]
        if allowed_only is not None:
            entries = [e for e in entries if e.allowed == allowed_only]
        return entries[:limit]

    def stats(self) -> dict[str, Any]:
        return {
            "total_audit_entries": len(self._audit_log),
            "denied_count": sum(1 for e in self._audit_log if not e.allowed),
            "principals_registered": len(self._principals),
            "roles_defined": len(self._roles),
        }
