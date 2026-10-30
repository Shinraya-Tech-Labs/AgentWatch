"""
PRT-001 — Open Reasoning Trace Schema (ReasoningTrace v1.0).

A framework-neutral, fully-documented schema for agent reasoning traces.
Exposed as JSON Schema so any toolchain can validate against it.
"""

from __future__ import annotations

from typing import Any

REASONING_TRACE_VERSION = "1.0.0"


def reasoning_trace_schema() -> dict[str, Any]:
    """Return the JSON Schema document for ReasoningTrace v1.0."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://panoptis.dev/schemas/reasoning-trace/v1.0.0",
        "title": "ReasoningTrace",
        "description": (
            "Framework-neutral schema for capturing AI agent reasoning. "
            "Designed for interoperability across agent frameworks."
        ),
        "type": "object",
        "required": ["version", "trace_id", "agent", "spans"],
        "properties": {
            "version": {"const": REASONING_TRACE_VERSION},
            "trace_id": {"type": "string", "description": "Unique trace identifier."},
            "session_id": {"type": "string"},
            "started_at": {"type": "string", "format": "date-time"},
            "ended_at": {"type": "string", "format": "date-time"},
            "agent": {
                "type": "object",
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "framework": {"type": "string"},
                    "model": {"type": "string"},
                },
            },
            "goal": {"type": "string", "description": "User's stated objective."},
            "spans": {
                "type": "array",
                "items": {"$ref": "#/$defs/Span"},
            },
            "outcome": {
                "type": "object",
                "properties": {
                    "status": {"enum": ["success", "failure", "blocked", "timeout"]},
                    "summary": {"type": "string"},
                },
            },
        },
        "$defs": {
            "Span": {
                "type": "object",
                "required": ["span_id", "kind", "name", "start_time"],
                "properties": {
                    "span_id": {"type": "string"},
                    "parent_span_id": {"type": ["string", "null"]},
                    "kind": {
                        "enum": ["reasoning", "tool_call", "memory_read", "model_call", "generic"]
                    },
                    "name": {"type": "string"},
                    "start_time": {"type": "string", "format": "date-time"},
                    "end_time": {"type": ["string", "null"], "format": "date-time"},
                    "input": {},
                    "output": {},
                    "error": {"type": ["string", "null"]},
                    "token_count": {"type": "integer", "minimum": 0},
                    "retry_count": {"type": "integer", "minimum": 0},
                    "attributes": {"type": "object", "additionalProperties": True},
                },
            }
        },
    }


def validate_trace(trace: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Minimal structural validation against ReasoningTrace v1.0.
    Returns (ok, errors).
    """
    errors: list[str] = []

    def require(obj: dict, key: str, where: str) -> Any:
        if key not in obj:
            errors.append(f"{where}: missing required field '{key}'")
            return None
        return obj[key]

    if not isinstance(trace, dict):
        return False, ["root is not an object"]

    version = require(trace, "version", "root")
    if version is not None and version != REASONING_TRACE_VERSION:
        errors.append(f"root.version: expected '{REASONING_TRACE_VERSION}', got '{version}'")

    require(trace, "trace_id", "root")

    agent = require(trace, "agent", "root")
    if isinstance(agent, dict):
        require(agent, "id", "agent")
        require(agent, "name", "agent")
    elif agent is not None:
        errors.append("agent: must be an object")

    spans = require(trace, "spans", "root")
    if isinstance(spans, list):
        kinds = {"reasoning", "tool_call", "memory_read", "model_call", "generic"}
        for idx, span in enumerate(spans):
            if not isinstance(span, dict):
                errors.append(f"spans[{idx}]: must be an object")
                continue
            for field in ("span_id", "kind", "name", "start_time"):
                if field not in span:
                    errors.append(f"spans[{idx}]: missing '{field}'")
            if span.get("kind") not in kinds:
                errors.append(f"spans[{idx}]: invalid kind '{span.get('kind')}'")
    elif spans is not None:
        errors.append("spans: must be an array")

    return (len(errors) == 0, errors)


__all__ = [
    "REASONING_TRACE_VERSION",
    "reasoning_trace_schema",
    "validate_trace",
]
