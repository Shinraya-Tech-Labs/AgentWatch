"""Model Context Protocol (MCP) error classification and normalization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Any


class MCPErrorCode(IntEnum):
    """Standard JSON-RPC 2.0 error codes used in MCP."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    SERVER_ERROR_MIN = -32099
    SERVER_ERROR_MAX = -32000


class MCPErrorCategory(str, Enum):
    """High-level semantic categories for MCP errors."""

    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    DOWNSTREAM_TIMEOUT = "DOWNSTREAM_TIMEOUT"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


@dataclass(frozen=True)
class MCPErrorEnvelope:
    """Normalized MCP error envelope preserving code, category, message, and diagnostic data."""

    code: int
    category: MCPErrorCategory
    message: str
    data: Any = None


def classify_mcp_error(response: dict[str, Any]) -> MCPErrorEnvelope:
    """Classify an MCP JSON-RPC response or error payload into a typed MCPErrorEnvelope."""
    err = response.get("error") if isinstance(response, dict) and "error" in response else response
    if not isinstance(err, dict):
        return MCPErrorEnvelope(
            code=int(MCPErrorCode.INTERNAL_ERROR),
            category=MCPErrorCategory.INTERNAL_SERVER_ERROR,
            message=str(err) if err is not None else "Unknown error",
        )

    code_raw = err.get("code")
    try:
        code = int(code_raw) if code_raw is not None else int(MCPErrorCode.INTERNAL_ERROR)
    except (ValueError, TypeError):
        code = int(MCPErrorCode.INTERNAL_ERROR)

    msg = str(err.get("message") or "")
    data = err.get("data")
    search_text = f"{msg} {data}".lower() if data is not None else msg.lower()

    if "timeout" in search_text or "timed out" in search_text:
        category = MCPErrorCategory.DOWNSTREAM_TIMEOUT
    elif code in (
        MCPErrorCode.PARSE_ERROR,
        MCPErrorCode.INVALID_REQUEST,
        MCPErrorCode.INVALID_PARAMS,
    ):
        category = MCPErrorCategory.SCHEMA_VIOLATION
    elif code == MCPErrorCode.METHOD_NOT_FOUND:
        category = MCPErrorCategory.TOOL_NOT_FOUND
    else:
        category = MCPErrorCategory.INTERNAL_SERVER_ERROR

    return MCPErrorEnvelope(code=code, category=category, message=msg or "MCP error", data=data)
