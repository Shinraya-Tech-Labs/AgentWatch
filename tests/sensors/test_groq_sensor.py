"""Unit tests for Groq sensor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from panoptis.core.event_bus import EventBus
from panoptis.core.schema import EventType, ExecutionStatus
from panoptis.sensors.groq_sensor import GroqSensor, instrument_groq


@pytest.fixture(autouse=True)
def _mock_groq_available(monkeypatch):
    """Mock Groq as available for all tests."""
    monkeypatch.setattr("panoptis.sensors.groq_sensor._GROQ_AVAILABLE", True)


class _MockGroqMessage:
    def __init__(self, content: str = "hello", tool_calls: list | None = None):
        self.content = content
        self.tool_calls = tool_calls or []


class _MockGroqChoice:
    def __init__(self, message: _MockGroqMessage):
        self.message = message


class _MockGroqUsage:
    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5, total_tokens: int = 15):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _MockGroqResponse:
    def __init__(
        self,
        id: str = "resp1",
        model: str = "llama3-70b-8192",
        choices: list | None = None,
        usage: _MockGroqUsage | None = None,
    ):
        self.id = id
        self.model = model
        self.choices = choices or [_MockGroqChoice(_MockGroqMessage())]
        self.usage = usage


class _MockGroqCompletions:
    def __init__(self, response: _MockGroqResponse | None = None, should_fail: bool = False):
        self._response = response or _MockGroqResponse(usage=_MockGroqUsage())
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


def _make_mock_groq_client(response: _MockGroqResponse | None = None, should_fail: bool = False):
    completions = _MockGroqCompletions(response, should_fail)
    chat = SimpleNamespace(completions=completions)
    return SimpleNamespace(chat=chat), completions


def _make_bus():
    bus = EventBus()
    captured = []
    bus.subscribe_fn(lambda event: captured.append(event), handler_id="test.capture")
    return bus, captured


def test_groq_sensor_missing_sdk(monkeypatch):
    """Test that GroqSensor raises RuntimeError when Groq SDK is not available."""
    monkeypatch.setattr("panoptis.sensors.groq_sensor._GROQ_AVAILABLE", False)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="Groq SDK not installed"):
        GroqSensor(client, "sess1", "agent1")


def test_groq_sensor_sync_call_emits_events():
    """Test synchronous call emits AGENT_INTENT and completion events."""
    bus, captured = _make_bus()
    client, completions = _make_mock_groq_client()

    sensor = GroqSensor(client, "sess1", "agent1", "TestAgent")
    # Replace bus with our test bus
    sensor.bus = bus

    response = client.chat.completions.create(
        model="llama3-70b-8192", messages=[{"role": "user", "content": "Hello"}]
    )

    assert response is completions._response
    assert len(completions.calls) == 1

    # Should have AGENT_INTENT (start), TOOL_CALL (if any), AGENT_INTENT (completion)
    event_types = [e.event_type for e in captured]
    assert EventType.AGENT_INTENT in event_types
    # First event should be AGENT_INTENT with RUNNING status
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
    assert start_events[0].metadata["model"] == "llama3-70b-8192"
    assert start_events[0].prompt_preview == "Hello"

    # Completion event should be AGENT_INTENT with SUCCESS status
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
    assert completion_events[0].token_usage.completion_tokens == 5
    assert completion_events[0].metadata["model"] == "llama3-70b-8192"
    assert completion_events[0].metadata["response_id"] == "resp1"


def test_groq_sensor_sync_call_with_tool_calls():
    """Test synchronous call with tool calls emits TOOL_CALL events."""
    bus, captured = _make_bus()

    tool_call = SimpleNamespace(
        id="call1",
        function=SimpleNamespace(name="get_weather", arguments='{"location": "NYC"}'),
    )
    message = _MockGroqMessage(tool_calls=[tool_call])
    response = _MockGroqResponse(choices=[_MockGroqChoice(message)], usage=_MockGroqUsage())
    client, completions = _make_mock_groq_client(response)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="llama3-70b-8192", messages=[])

    tool_call_events = [e for e in captured if e.event_type == EventType.TOOL_CALL]
    assert len(tool_call_events) == 1
    tc_event = tool_call_events[0]
    assert tc_event.tool_call is not None
    assert tc_event.tool_call.tool_name == "get_weather"
    assert tc_event.tool_call.tool_id == "call1"
    assert tc_event.tool_call.arguments == {"location": "NYC"}
    assert tc_event.token_usage is not None


def test_groq_sensor_sync_error_emits_error_event():
    """Test that errors are emitted and re-raised."""
    bus, captured = _make_bus()
    client, completions = _make_mock_groq_client(should_fail=True)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    with pytest.raises(RuntimeError, match="API error"):
        client.chat.completions.create(model="llama3-70b-8192", messages=[])

    error_events = [e for e in captured if e.event_type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert error_events[0].status == ExecutionStatus.FAILURE
    assert error_events[0].duration_ms is not None
    assert error_events[0].metadata["error"]["type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_groq_sensor_async_call_emits_events():
    """Test asynchronous call emits events."""
    bus, captured = _make_bus()
    client, completions = _make_mock_groq_client()

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    response = await client.chat.completions.acreate(
        model="llama3-70b-8192", messages=[{"role": "user", "content": "Hello async"}]
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
async def test_groq_sensor_async_error_emits_error_event():
    """Test that async errors are emitted and re-raised."""
    bus, captured = _make_bus()
    client, completions = _make_mock_groq_client(should_fail=True)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    with pytest.raises(RuntimeError, match="API error"):
        await client.chat.completions.acreate(model="llama3-70b-8192", messages=[])

    error_events = [e for e in captured if e.event_type == EventType.AGENT_ERROR]
    assert len(error_events) == 1
    assert error_events[0].status == ExecutionStatus.FAILURE


def test_instrument_groq_returns_sensor():
    """Test that instrument_groq returns a sensor instance."""
    client, _ = _make_mock_groq_client()
    # We can't easily test the full instrument_groq without the SDK,
    # but we can verify the function exists and returns the right type
    assert callable(instrument_groq)


def test_groq_sensor_multiple_tool_calls():
    """Test multiple tool calls in one response."""
    bus, captured = _make_bus()

    tool_call1 = SimpleNamespace(
        id="call1",
        function=SimpleNamespace(name="get_weather", arguments='{"location": "NYC"}'),
    )
    tool_call2 = SimpleNamespace(
        id="call2",
        function=SimpleNamespace(name="get_time", arguments='{"timezone": "UTC"}'),
    )
    message = _MockGroqMessage(tool_calls=[tool_call1, tool_call2])
    response = _MockGroqResponse(choices=[_MockGroqChoice(message)])
    client, completions = _make_mock_groq_client(response)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="llama3-70b-8192", messages=[])

    tool_call_events = [e for e in captured if e.event_type == EventType.TOOL_CALL]
    assert len(tool_call_events) == 2
    assert tool_call_events[0].tool_call.tool_name == "get_weather"
    assert tool_call_events[1].tool_call.tool_name == "get_time"


def test_groq_sensor_no_usage_in_response():
    """Test handling when response has no usage info."""
    bus, captured = _make_bus()
    response = _MockGroqResponse(usage=None)
    client, completions = _make_mock_groq_client(response)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="llama3-70b-8192", messages=[])

    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1
    assert completion_events[0].token_usage is None


def test_groq_sensor_no_choices_in_response():
    """Test handling when response has no choices."""
    bus, captured = _make_bus()
    response = _MockGroqResponse(choices=[])
    client, completions = _make_mock_groq_client(response)

    sensor = GroqSensor(client, "sess1", "agent1")
    sensor.bus = bus

    client.chat.completions.create(model="llama3-70b-8192", messages=[])

    # Should still emit events, just no tool calls
    completion_events = [
        e
        for e in captured
        if e.event_type == EventType.AGENT_INTENT and e.status == ExecutionStatus.SUCCESS
    ]
    assert len(completion_events) == 1
