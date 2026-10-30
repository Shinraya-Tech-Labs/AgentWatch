"""
PLT-008 — Plugin System (registry, install/uninstall).

Third-party auditor models, custom safety rule packs, plugin registry.
Builds on the existing `panoptis.core.schema.PluginManifest`.
"""

from __future__ import annotations

import builtins
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from panoptis.core.schema import PluginManifest


@dataclass
class PluginRecord:
    manifest: PluginManifest
    installed_at: datetime
    enabled: bool = True
    handler: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginRegistry:
    """In-memory plugin registry with checksum verification."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRecord] = {}

    def install(
        self,
        manifest: PluginManifest,
        *,
        payload: bytes | None = None,
        handler: Any = None,
    ) -> PluginRecord:
        # Verify checksum if both are present
        if payload is not None and manifest.checksum_sha256:
            actual = hashlib.sha256(payload).hexdigest()
            if actual != manifest.checksum_sha256:
                raise ValueError(
                    f"checksum mismatch: expected {manifest.checksum_sha256}, got {actual}"
                )
        record = PluginRecord(
            manifest=manifest,
            installed_at=datetime.now(UTC),
            handler=handler,
        )
        self._plugins[manifest.plugin_id] = record
        return record

    def uninstall(self, plugin_id: str) -> bool:
        return self._plugins.pop(plugin_id, None) is not None

    def enable(self, plugin_id: str) -> bool:
        record = self._plugins.get(plugin_id)
        if record is None:
            return False
        record.enabled = True
        return True

    def disable(self, plugin_id: str) -> bool:
        record = self._plugins.get(plugin_id)
        if record is None:
            return False
        record.enabled = False
        return True

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._plugins.get(plugin_id)

    def list(self, *, enabled_only: bool = False) -> builtins.list[PluginRecord]:
        records = list(self._plugins.values())
        if enabled_only:
            records = [r for r in records if r.enabled]
        return records

    def by_trust_level(self, min_trust: int) -> builtins.list[PluginRecord]:
        return [r for r in self._plugins.values() if r.manifest.trust_level >= min_trust]

    def to_json(self) -> str:
        return json.dumps(
            [
                {
                    "plugin_id": r.manifest.plugin_id,
                    "name": r.manifest.name,
                    "version": r.manifest.version,
                    "trust_level": r.manifest.trust_level,
                    "installed_at": r.installed_at.isoformat(),
                    "enabled": r.enabled,
                }
                for r in self._plugins.values()
            ]
        )


__all__ = ["PluginRegistry", "PluginRecord"]
