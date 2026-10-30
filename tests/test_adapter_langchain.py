from __future__ import annotations

import asyncio
import uuid

from panoptis.adapters.langchain import AgentWatchCallbackHandler
from panoptis.core.event_bus import EventBus
from panoptis.core.schema import EventType, ExecutionStatus


def _drain() -> None:
    asyncio.run(asyncio.sleep(0.01))


def test_langchain_handler_emits_chain_and_tool_events() -> None:
    bus = EventBus()
    captured = []
    bus.subscribe_fn(lambda event: captured.append(event), handler_id="test.capture")

    handler = AgentWatchCallbackHandler(
        session_id="session-123",
        agent_id="agent-123",
        event_bus=bus,
    )

    chain_run_id = uuid.uuid4()
    tool_run_id = uuid.uuid4()

    handler.on_chain_start(
        {"name": "AgentExecutor"},
        {"input": "summarize the logs"},
        run_id=chain_run_id,
    )
    handler.on_tool_start(
        {"name": "terminal"},
        "echo hello",
        run_id=tool_run_id,
        parent_run_id=chain_run_id,
    )
    handler.on_tool_end(
        "hello\n",
        run_id=tool_run_id,
        parent_run_id=chain_run_id,
    )
    handler.on_chain_end(
        {"output": "done"},
        run_id=chain_run_id,
    )

    _drain()

    assert [event.event_type for event in captured] == [
        EventType.AGENT_START,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.AGENT_END,
    ]

    start_event, tool_call_event, tool_result_event, end_event = captured

    assert start_event.session_id == "session-123"
    assert start_event.agent_id == "agent-123"
    assert start_event.goal == "summarize the logs"
    assert start_event.metadata["chain_type"] == "AgentExecutor"

    assert tool_call_event.tool_call is not None
    assert tool_call_event.tool_call.tool_name == "terminal"
    assert tool_call_event.tool_call.arguments == {"input": "echo hello"}
    assert tool_call_event.tool_call.raw_command == "echo hello"

    assert tool_result_event.status == ExecutionStatus.SUCCESS
    assert tool_result_event.tool_result is not None
    assert tool_result_event.tool_result.output == "hello\n"

    assert end_event.status == ExecutionStatus.SUCCESS
    assert end_event.metadata["output_preview"] == "done"


def test_langchain_handler_emits_agent_error_on_chain_error() -> None:
    bus = EventBus()
    captured = []
    bus.subscribe_fn(lambda event: captured.append(event), handler_id="test.capture")

    handler = AgentWatchCallbackHandler(event_bus=bus)

    handler.on_chain_error(RuntimeError("boom"), run_id=uuid.uuid4())
    _drain()

    assert len(captured) == 1
    assert captured[0].event_type == EventType.AGENT_ERROR
    assert captured[0].status == ExecutionStatus.FAILURE
    assert captured[0].metadata["error"] == "boom"
