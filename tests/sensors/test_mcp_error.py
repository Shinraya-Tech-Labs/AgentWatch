"""Unit tests for Model Context Protocol (MCP) error classification."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from panoptis.sensors.mcp_error import (
    MCPErrorCategory,
    MCPErrorCode,
    MCPErrorEnvelope,
    classify_mcp_error,
)


def test_standard_jsonrpc_parse_error():
    payload = {"error": {"code": -32700, "message": "Parse error"}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.PARSE_ERROR
    assert envelope.category == MCPErrorCategory.SCHEMA_VIOLATION
    assert envelope.message == "Parse error"
    assert envelope.data is None


def test_standard_jsonrpc_invalid_request():
    payload = {"error": {"code": -32600, "message": "Invalid Request"}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.INVALID_REQUEST
    assert envelope.category == MCPErrorCategory.SCHEMA_VIOLATION
    assert envelope.message == "Invalid Request"


def test_standard_jsonrpc_invalid_params():
    payload = {"error": {"code": -32602, "message": "Invalid params", "data": {"param": "query"}}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.INVALID_PARAMS
    assert envelope.category == MCPErrorCategory.SCHEMA_VIOLATION
    assert envelope.message == "Invalid params"
    assert envelope.data == {"param": "query"}


def test_standard_jsonrpc_method_not_found():
    payload = {"error": {"code": -32601, "message": "Method not found"}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.METHOD_NOT_FOUND
    assert envelope.category == MCPErrorCategory.TOOL_NOT_FOUND
    assert envelope.message == "Method not found"


def test_standard_jsonrpc_internal_error():
    payload = {"error": {"code": -32603, "message": "Internal error"}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.INTERNAL_ERROR
    assert envelope.category == MCPErrorCategory.INTERNAL_SERVER_ERROR
    assert envelope.message == "Internal error"


@pytest.mark.parametrize(
    "code",
    [
        MCPErrorCode.SERVER_ERROR_MIN,
        MCPErrorCode.SERVER_ERROR_MAX,
        -32050,
    ],
)
def test_server_error_range(code):
    payload = {"error": {"code": code, "message": f"Server error {code}"}}
    envelope = classify_mcp_error(payload)
    assert envelope.code == code
    assert envelope.category == MCPErrorCategory.INTERNAL_SERVER_ERROR


def test_timeout_in_message():
    payload = {"error": {"code": -32000, "message": "Upstream server timeout"}}
    envelope = classify_mcp_error(payload)
    assert envelope.category == MCPErrorCategory.DOWNSTREAM_TIMEOUT

    payload_timed_out = {"error": {"code": -32603, "message": "Gateway call timed out"}}
    envelope_timed_out = classify_mcp_error(payload_timed_out)
    assert envelope_timed_out.category == MCPErrorCategory.DOWNSTREAM_TIMEOUT


def test_timeout_in_data():
    payload = {
        "error": {
            "code": -32001,
            "message": "Call failed",
            "data": {"reason": "Backend connection timed out"},
        }
    }
    envelope = classify_mcp_error(payload)
    assert envelope.category == MCPErrorCategory.DOWNSTREAM_TIMEOUT
    assert envelope.data == {"reason": "Backend connection timed out"}


def test_timeout_case_insensitive():
    payload = {"error": {"code": -32000, "message": "GATEWAY TIMEOUT OCCURRED"}}
    envelope = classify_mcp_error(payload)
    assert envelope.category == MCPErrorCategory.DOWNSTREAM_TIMEOUT


def test_vendor_extension_code_and_diagnostics():
    payload = {
        "jsonrpc": "2.0",
        "id": "req-99",
        "error": {
            "code": 4004,
            "message": "Resource unavailable in region",
            "data": {"region": "us-east-1", "retries": 3},
        },
    }
    envelope = classify_mcp_error(payload)
    assert envelope.code == 4004
    assert envelope.category == MCPErrorCategory.INTERNAL_SERVER_ERROR
    assert envelope.message == "Resource unavailable in region"
    assert envelope.data == {"region": "us-east-1", "retries": 3}


def test_direct_error_dict():
    payload = {"code": -32601, "message": "Tool does not exist"}
    envelope = classify_mcp_error(payload)
    assert envelope.code == MCPErrorCode.METHOD_NOT_FOUND
    assert envelope.category == MCPErrorCategory.TOOL_NOT_FOUND
    assert envelope.message == "Tool does not exist"


def test_malformed_non_dict_payload():
    assert classify_mcp_error(None).code == MCPErrorCode.INTERNAL_ERROR
    assert classify_mcp_error(None).category == MCPErrorCategory.INTERNAL_SERVER_ERROR

    string_env = classify_mcp_error("Unexpected server failure")  # type: ignore[arg-type]
    assert string_env.code == MCPErrorCode.INTERNAL_ERROR
    assert string_env.category == MCPErrorCategory.INTERNAL_SERVER_ERROR
    assert string_env.message == "Unexpected server failure"

    list_env = classify_mcp_error(["error", 123])  # type: ignore[arg-type]
    assert list_env.code == MCPErrorCode.INTERNAL_ERROR
    assert list_env.category == MCPErrorCategory.INTERNAL_SERVER_ERROR


def test_malformed_code_fallback():
    # Missing code field
    env_no_code = classify_mcp_error({"message": "Something went wrong"})
    assert env_no_code.code == MCPErrorCode.INTERNAL_ERROR

    # Non-integer code
    env_str_code = classify_mcp_error({"code": "invalid_number", "message": "Bad code"})
    assert env_str_code.code == MCPErrorCode.INTERNAL_ERROR

    # String-encoded integer code
    env_parsed_code = classify_mcp_error({"code": "-32601", "message": "Method missing"})
    assert env_parsed_code.code == MCPErrorCode.METHOD_NOT_FOUND
    assert env_parsed_code.category == MCPErrorCategory.TOOL_NOT_FOUND


def test_empty_message_fallback():
    envelope = classify_mcp_error({"code": -32603, "message": ""})
    assert envelope.message == "MCP error"


def test_envelope_immutability():
    envelope = MCPErrorEnvelope(
        code=-32600,
        category=MCPErrorCategory.SCHEMA_VIOLATION,
        message="Invalid Request",
    )
    with pytest.raises(FrozenInstanceError):
        envelope.code = -32601  # type: ignore[misc]


def test_enum_constants():
    assert MCPErrorCode.PARSE_ERROR == -32700
    assert MCPErrorCode.INVALID_REQUEST == -32600
    assert MCPErrorCode.METHOD_NOT_FOUND == -32601
    assert MCPErrorCode.INVALID_PARAMS == -32602
    assert MCPErrorCode.INTERNAL_ERROR == -32603
    assert MCPErrorCode.SERVER_ERROR_MIN == -32099
    assert MCPErrorCode.SERVER_ERROR_MAX == -32000

    assert MCPErrorCategory.SCHEMA_VIOLATION.value == "SCHEMA_VIOLATION"
    assert MCPErrorCategory.TOOL_NOT_FOUND.value == "TOOL_NOT_FOUND"
    assert MCPErrorCategory.DOWNSTREAM_TIMEOUT.value == "DOWNSTREAM_TIMEOUT"
    assert MCPErrorCategory.INTERNAL_SERVER_ERROR.value == "INTERNAL_SERVER_ERROR"
