"""Unit tests for OpenRouter sensor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from panoptis.core.event_bus import EventBus
from panoptis.core.schema import EventType, ExecutionStatus
from panoptis.sensors.openrouter_sensor import (
    OpenRouterSensor,
    _is_openrouter_client,
    instrument_openrouter,
)


@pytest.fixture(autouse=True)
def _mock_openai_available(monkeypatch):
    """Mock OpenAI as available for all tests."""
    monkeypatch.setattr("panoptis.sensors.openrouter_sensor._OPENAI_AVAILABLE", True)


class _MockOpenAIMessage:
    def __init__(self, content: str = "hello", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _MockOpenAIChoice:
    def __init__(self, message: _MockOpenAIMessage):
        self.message = message


class _MockOpenAIUsage:
    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5, total_tokens: int = 15):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _MockOpenAIResponse:
    def __init__(
        self,
        id: str = "resp1",
        model: str = "openrouter/llama-3.1-70b",
        choices: list | None = None,
        usage: _MockOpenAIUsage | None = None,
    ):
        self.id = id
        self.model = model
        self.choices = choices or [_MockOpenAIChoice(_MockOpenAIMessage())]
        self.usage = usage


class _MockOpenAICompletions:
    def __init__(self, response: _MockOpenAIResponse | None = None, should_fail: bool = False):
        self._response = response or _MockOpenAIResponse(usage=_MockOpenAIUsage())
        self._should_fail = should_fail
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._should_fail:
            raise RuntimeError("API error")
        return self._response

    async def acreate(self, **kwargs):
        self.calls.append(kwargs)
        if self._should_fail:
            raise RuntimeError("API error")
        return self._response


def _make_mock_openrouter_client(
    response: _MockOpenAIResponse | None = None, should_fail: bool = False
):
    completions = _MockOpenAICompletions(response, should_fail)
    chat = SimpleNamespace(completions=completions)
    client = SimpleNamespace(chat=chat, base_url="https://openrouter.ai/api/v1")
    return client, completions


def _make_mock_regular_openai_client(
    response: _MockOpenAIResponse | None = None, should_fail: bool = False
):
    completions = _MockOpenAICompletions(response, should_fail)
    chat = SimpleNamespace(completions=completions)
    client = SimpleNamespace(chat=chat, base_url="https://api.openai.com/v1")
    return client, completions


def _make_bus():
    bus = EventBus()
    captured = []
    bus.subscribe_fn(lambda event: captured.append(event), handler_id="test.capture")
    return bus, captured


def test_is_openrouter_client_true():
    """Test detection of OpenRouter client."""
    client = SimpleNamespace(base_url="https://openrouter.ai/api/v1")
    assert _is_openrouter_client(client) is True

    client2 = SimpleNamespace(base_url="https://openrouter.com/v1")
    assert _is_openrouter_client(client2) is True


def test_is_openrouter_client_false():
    """Test detection rejects regular OpenAI client."""
    client = SimpleNamespace(base_url="https://api.openai.com/v1")
    assert _is_openrouter_client(client) is False

    client2 = SimpleNamespace(base_url="https://custom.example.com")
    assert _is_openrouter_client(client2) is False


def test_openrouter_sensor_missing_sdk(monkeypatch):
    """Test that OpenRouterSensor raises RuntimeError when OpenAI SDK is not available."""
    monkeypatch.setattr("panoptis.sensors.openrouter_sensor._OPENAI_AVAILABLE", False)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace()), base_url="https://openrouter.ai/api/v1"
    )
    with pytest.raises(RuntimeError, match="OpenAI SDK not installed"):
        OpenRouterSensor(client, "sess1", "agent1")


def test_openrouter_sensor_rejects_non_openrouter_client():
    """Test that OpenRouterSensor raises ValueError for non-OpenRouter clients."""
    client = SimpleNamespace(base_url="https://api.openai.com/v1")
    with pytest.raises(ValueError, match="configured for OpenRouter"):
        OpenRouterSensor(client, "sess1", "agent1")


def test_openrouter_sensor_sync_call_emits_events():
    """Test synchronous call emits AGENT_INTENT and completion events."""
    bus, captured = _make_bus()
    client, completions = _make_mock_openrouter_client()

    sensor = OpenRouterSensor(client, "sess1", "agent1", "TestAgent")
    sensor.bus = bus

    response = client.chat.completions.create(
        model="openrouter/llama-3.1-70b", messages=[{"role": "user", "content": "Hello"}]
    )

    assert response is completions._response
    assert len(completions.calls) == 1

    event_types = [e.event_type for e in captured]
    assert EventType.AGENT_INTENT in event_types

    start_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.RUNNING
    ]
    assert len(start_events) == 1
    assert start_events[0].session_id == "sess1"
    assert start_events[0].agent_id == "agent1"
    assert start_events[0].agent_name == "TestAgent"
    assert start_events[0].metadata["operation"] == "chat.completions.create"
    assert start_events[0].metadata["model"] == "openrouter/llama-3.1-70b"
    assert start_events[0].metadata["provider"] == "openrouter"
    assert start_events[0].prompt_preview == "Hello"

    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1
    assert completion_events[0].duration_ms is not None
    assert completion_events[0].duration_ms > 0
    assert completion_events[0].token_usage is not None
    assert completion_events[0].token_usage.prompt_tokens == 10
    assert completion_events[0].metadata["provider"] == "openrouter"


def test_openrouter_sensor_sync_call_with_tool_calls():
    """Test synchronous call with tool calls emits TOOL_CALL events."""
    bus, captured = _make_bus()

    tool_call = SimpleNamespace(
        id="call1",
        function=SimpleNamespace(name="search_web", arguments='{"query": "weather NYC"}'),
    )
    message = _MockOpenAIMessage(tool_calls=[tool_call])
    response = _MockOpenAIResponse(choices=[_MockOpenAIChoice(message)])
    client, completions = _make_mock_openrouter_client(response)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="openrouter/llama-3.1-70b", messages=[])

    tool_call_events = [e for e in captured if e.event_type == EventType.TOOL_CALL]
    assert len(tool_call_events) == 1
    tc_event = tool_call_events[0]
    assert tc_event.tool_call is not None
    assert tc_event.tool_call.tool_name == "search_web"
    assert tc_event.tool_call.tool_id == "call1"
    assert tc_event.tool_call.arguments == {"query": "weather NYC"}
    assert tc_event.metadata["provider"] == "openrouter"


def test_openrouter_sensor_sync_error_emits_error_event():
    """Test that errors are emitted and re-raised."""
    bus, captured = _make_bus()
    client, completions = _make_mock_openrouter_client(should_fail=True)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    with pytest.raises(RuntimeError, match="API error"):
        client.chat.completions.create(model="openrouter/llama-3.1-70b", messages=[])

    error_events = [e for e in captured if e.event_type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert error_events[0].status == ExecutionStatus.FAILURE
    assert error_events[0].duration_ms is not None
    assert error_events[0].metadata["error"]["type"] == "RuntimeError"
    assert error_events[0].metadata["provider"] == "openrouter"


@pytest.mark.asyncio
async def test_openrouter_sensor_async_call_emits_events():
    """Test asynchronous call emits events."""
    bus, captured = _make_bus()
    client, completions = _make_mock_openrouter_client()

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    response = await client.chat.completions.acreate(
        model="openrouter/llama-3.1-70b", messages=[{"role": "user", "content": "Hello async"}]
    )

    assert response is completions._response
    assert len(completions.calls) == 1

    event_types = [e.event_type for e in captured]
    assert EventType.AGENT_INTENT in event_types

    start_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.RUNNING
    ]
    assert len(start_events) == 1
    assert start_events[0].prompt_preview == "Hello async"

    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1
    assert completion_events[0].duration_ms is not None
    assert completion_events[0].duration_ms > 0


@pytest.mark.asyncio
async def test_openrouter_sensor_async_error_emits_error_event():
    """Test that async errors are emitted and re-raised."""
    bus, captured = _make_bus()
    client, completions = _make_mock_openrouter_client(should_fail=True)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    with pytest.raises(RuntimeError, match="API error"):
        await client.chat.completions.acreate(model="openrouter/llama-3.1-70b", messages=[])

    error_events = [e for e in captured if e.event_type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert error_events[0].status == ExecutionStatus.FAILURE


def test_openrouter_sensor_multiple_tool_calls():
    """Test multiple tool calls in one response."""
    bus, captured = _make_bus()

    tool_call1 = SimpleNamespace(
        id="call1",
        function=SimpleNamespace(name="search_web", arguments='{"query": "weather NYC"}'),
    )
    tool_call2 = SimpleNamespace(
        id="call2",
        function=SimpleNamespace(name="get_time", arguments='{"timezone": "UTC"}'),
    )
    message = _MockOpenAIMessage(tool_calls=[tool_call1, tool_call2])
    response = _MockOpenAIResponse(choices=[_MockOpenAIChoice(message)])
    client, completions = _make_mock_openrouter_client(response)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="openrouter/llama-3.1-70b", messages=[])

    tool_call_events = [e for e in captured if e.event_type == EventType.TOOL_CALL]
    assert len(tool_call_events) == 2
    assert tool_call_events[0].tool_call.tool_name == "search_web"
    assert tool_call_events[1].tool_call.tool_name == "get_time"


def test_openrouter_sensor_no_usage_in_response():
    """Test handling when response has no usage info."""
    bus, captured = _make_bus()
    response = _MockOpenAIResponse(usage=None)
    client, completions = _make_mock_openrouter_client(response)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="openrouter/llama-3.1-70b", messages=[])

    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1
    assert completion_events[0].token_usage is None


def test_openrouter_sensor_no_choices_in_response():
    """Test handling when response has no choices."""
    bus, captured = _make_bus()
    response = _MockOpenAIResponse(choices=[])
    client, completions = _make_mock_openrouter_client(response)

    sensor = OpenRouterSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="openrouter/llama-3.1-70b", messages=[])

    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1


def test_instrument_openrouter_returns_sensor():
    """Test that instrument_openrouter function exists."""
    assert callable(instrument_openrouter)
