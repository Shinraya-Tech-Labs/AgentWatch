import pytest

from panoptis.adapters.openai_agents import AgentWatchOpenAIAgentsAdapter
from panoptis.core.event_bus import EventBus
from panoptis.core.schema import EventType, ExecutionStatus


def _make_bus():
    bus = EventBus()
    captured = []
    bus.subscribe_fn(
        lambda event: captured.append(event),
        handler_id="test.capture",
    )
    return bus, captured


def test_agent_start_emits_agent_start():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_agent_start(role="Researcher")

    assert len(captured) == 1
    assert captured[0].event_type == EventType.AGENT_START


def test_agent_end_emits_agent_end():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_agent_end(result="done")

    assert len(captured) == 1
    assert captured[0].event_type == EventType.AGENT_END
    assert captured[0].status == ExecutionStatus.SUCCESS


def test_tool_call_emits_tool_call():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_tool_call("calculator", input="2+2")

    assert len(captured) == 1
    assert captured[0].event_type == EventType.TOOL_CALL


def test_tool_result_emits_tool_result():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_tool_result("calculator", result="4")

    assert len(captured) == 1
    assert captured[0].event_type == EventType.TOOL_RESULT


def test_handoff_emits_planner_output():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_handoff("researcher", "writer")

    assert len(captured) == 1
    assert captured[0].event_type == EventType.PLANNER_OUTPUT


def test_agent_error_emits_failure():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    adapter.on_agent_error(RuntimeError("boom"))

    assert len(captured) == 1
    assert captured[0].event_type == EventType.AGENT_ERROR
    assert captured[0].status == ExecutionStatus.FAILURE


def test_tool_call_populates_tool_call_data():
    bus, captured = _make_bus()

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)
    # Testing with string input
    adapter.on_tool_call("calculator", input="2+2")
    # Testing with dict input
    adapter.on_tool_call("bash", input={"command": "rm -rf /"})

    assert len(captured) == 2

    # Check first tool call (string input)
    event1 = captured[0]
    assert event1.tool_call is not None
    assert event1.tool_call.tool_name == "calculator"
    assert event1.tool_call.arguments == {"input": "2+2"}
    assert event1.tool_call.raw_command is None

    # Check second tool call (dict input with command key)
    event2 = captured[1]
    assert event2.tool_call is not None
    assert event2.tool_call.tool_name == "bash"
    assert event2.tool_call.arguments == {"command": "rm -rf /"}
    assert event2.tool_call.raw_command == "rm -rf /"


@pytest.mark.asyncio
async def test_openai_agents_safety_bypass_regression():
    from panoptis.core.event_bus import EventBus
    from panoptis.core.safety import SafetyEngine

    bus = EventBus()
    safety = SafetyEngine()

    # Intercept tool calls through safety engine
    captured = []

    async def safety_handler(event):
        checked = await safety.check_event(event)
        captured.append(checked)

    bus.subscribe_fn(safety_handler, handler_id="test.safety")

    adapter = AgentWatchOpenAIAgentsAdapter(event_bus=bus)

    # Trigger a highly dangerous command
    adapter.on_tool_call("bash", input={"command": "rm -rf /"})

    # Wait a brief moment for async subscription
    import asyncio

    await asyncio.sleep(0.05)

    assert len(captured) == 1
    event = captured[0]

    # The event must be blocked by the safety engine!
    assert event.status == ExecutionStatus.BLOCKED
    assert event.safety is not None
    assert event.safety.blocked is True
    assert any("FS_DELETE_CRITICAL" in policy for policy in event.safety.matched_policies)
