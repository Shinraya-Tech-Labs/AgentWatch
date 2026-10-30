"""Task Parameter Schema Validation

Validates task parameters against agent-specific schemas to prevent
invalid data from causing execution failures.
"""

from __future__ import annotations

from typing import Any

import jsonschema


class SchemaValidator:
    """Validates task parameters against agent schemas."""

    def __init__(self) -> None:
        """Initialize schema validator."""
        self.schemas: dict[str, dict[str, Any]] = {}

    def validate_task_parameters(
        self, agent_type: str, parameters: dict[str, Any]
    ) -> tuple[bool, str | None]:
        """Validate task parameters against schema."""
        schema = self.schemas.get(agent_type)
        if not schema:
            return False, f"No schema found for {agent_type}"
        try:
            jsonschema.validate(instance=parameters, schema=schema)
            return True, None
        except jsonschema.ValidationError as e:
            return False, f"Validation failed: {e.message}"

    def get_schema(self, agent_type: str) -> dict[str, Any] | None:
        """Get schema for agent type."""
        return self.schemas.get(agent_type)
