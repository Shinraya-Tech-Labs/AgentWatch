"""Tests for schema validation integration with API server."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from panoptis.api.server import _init_default_schemas, _schema_validator, app
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentSession,
    EventType,
    ToolCallData,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_session():
    return AgentSession(
        session_id="validation-test-session",
        agent_id="test-agent",
        agent_name="Test Agent",
        framework=AgentFramework.CLAUDE_CODE,
        goal="Test schema validation",
    )


class TestSchemaValidatorInitialization:
    """Test that SchemaValidator is initialized with default schemas."""

    def test_default_schemas_loaded(self, client):
        _init_default_schemas()
        assert "claude-code" in _schema_validator.schemas
        assert "langchain" in _schema_validator.schemas
        assert "crewai" in _schema_validator.schemas

    def test_claude_code_schema_structure(self, client):
        _init_default_schemas()
        schema = _schema_validator.get_schema("claude-code")
        assert schema is not None
        assert "properties" in schema
        assert "tool_name" in schema["properties"]
        assert "arguments" in schema["properties"]

    def test_langchain_schema_structure(self, client):
        _init_default_schemas()
        schema = _schema_validator.get_schema("langchain")
        assert schema is not None
        assert "properties" in schema
        assert "tool_name" in schema["properties"]

    def test_crewai_schema_structure(self, client):
        _init_default_schemas()
        schema = _schema_validator.get_schema("crewai")
        assert schema is not None
        assert "properties" in schema
        assert "tool_name" in schema["properties"]


class TestToolCallValidation:
    """Test tool call parameter validation during event ingestion."""

    def test_valid_tool_call_accepted(self, client, sample_session):
        client.post("/api/v1/sessions", json=sample_session.model_dump(mode="json"))

        event = AgentEvent(
            session_id="validation-test-session",
            agent_id="test-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(
                tool_name="bash",
                raw_command="ls -la",
                arguments={"command": "ls -la"},
            ),
        )
        response = client.post("/api/v1/events", json=event.model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

    def test_valid_langchain_tool_call(self, client):
        _init_default_schemas()
        session = AgentSession(
            session_id="langchain-test",
            agent_id="langchain-agent",
            agent_name="LangChain Agent",
            framework=AgentFramework.LANGCHAIN,
            goal="Test LangChain validation",
        )
        client.post("/api/v1/sessions", json=session.model_dump(mode="json"))

        event = AgentEvent(
            session_id="langchain-test",
            agent_id="langchain-agent",
            framework=AgentFramework.LANGCHAIN,
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(
                tool_name="Calculator",
                raw_command="calc",
                arguments={"tool": "Calculator", "tool_input": "2+2"},
            ),
        )
        response = client.post("/api/v1/events", json=event.model_dump(mode="json"))
        if response.status_code != 200:
            print(f"LangChain Error: {response.json()}")
        assert response.status_code == 200

    def test_non_tool_events_bypass_validation(self, client, sample_session):
        client.post("/api/v1/sessions", json=sample_session.model_dump(mode="json"))

        event = AgentEvent(
            session_id="validation-test-session",
            agent_id="test-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.PLANNER_OUTPUT,
            planner_output_preview="Planning the next steps...",
        )
        response = client.post("/api/v1/events", json=event.model_dump(mode="json"))
        assert response.status_code == 200

    def test_tool_result_events_bypass_validation(self, client, sample_session):
        client.post("/api/v1/sessions", json=sample_session.model_dump(mode="json"))

        event = AgentEvent(
            session_id="validation-test-session",
            agent_id="test-agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_RESULT,
            duration_ms=100,
        )
        response = client.post("/api/v1/events", json=event.model_dump(mode="json"))
        assert response.status_code == 200


class TestCustomSchemas:
    """Test registering and using custom schemas."""

    def test_register_custom_schema(self, sample_session):
        custom_schema = {
            "type": "object",
            "properties": {
                "tool_name": {"type": "string"},
                "custom_field": {"type": "string"},
            },
            "required": ["tool_name", "custom_field"],
        }
        _schema_validator.schemas["custom-agent"] = custom_schema

        AgentSession(
            session_id="custom-test",
            agent_id="custom-agent",
            agent_name="Custom Agent",
            framework=AgentFramework.CUSTOM,
            goal="Test custom schema",
        )
        assert _schema_validator.get_schema("custom-agent") is not None

    def test_unknown_agent_type_validation_skipped(self, client):
        session = AgentSession(
            session_id="unknown-test",
            agent_id="unknown-agent",
            agent_name="Unknown Agent",
            framework=AgentFramework.CUSTOM,
            goal="Test unknown agent",
        )
        client.post("/api/v1/sessions", json=session.model_dump(mode="json"))

        event = AgentEvent(
            session_id="unknown-test",
            agent_id="unknown-agent",
            framework=AgentFramework.CUSTOM,
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(
                tool_name="unknown_tool",
                raw_command="test",
                arguments={},
            ),
        )
        response = client.post("/api/v1/events", json=event.model_dump(mode="json"))
        assert response.status_code == 200
