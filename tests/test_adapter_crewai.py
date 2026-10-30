from __future__ import annotations

import sys
import types
import uuid

# ── Stub CrewAI namespace (must happen before any adapter import) ─────────────


class FakeAgent:
    """Minimal stand-in for crewai.Agent."""

    def __init__(self, role: str, tools: list | None = None):
        self.role = role
        self.tools = tools or []


class FakeTool:
    """Minimal stand-in for a crewai tool."""

    def __init__(self, name: str):
        self.name = name

    def run(self, input: str = "") -> str:
        return f"result_from_{self.name}"


class FakeCrew:
    """
    Minimal stand-in for crewai.Crew.

    kickoff() drives the adapter hooks manually in the same lifecycle order
    that real CrewAI triggers them:
        on_agent_start  → SESSION_START
        on_tool_start   → TOOL_CALL
        on_tool_end     → TOOL_RESULT
        on_agent_finish → SESSION_END
    """

    def __init__(
        self,
        agents: list[FakeAgent] | None = None,
        adapter=None,
    ):
        self.agents = agents or []
        self._adapter = adapter  # injected so kickoff() can fire hooks

    def kickoff(self) -> str:
        if self._adapter is None:
            return "done"

        # Mirrors the CrewAI lifecycle callback sequence
        self._adapter.on_agent_start(role=self.agents[0].role if self.agents else "unknown")

        for agent in self.agents:
            for tool in agent.tools:
                self._adapter.on_tool_start(tool.name, input="test input")
                result = tool.run(input="test input")
                self._adapter.on_tool_end(tool.name, result=result)

        self._adapter.on_agent_finish(result="crew finished")
        return "done"


# Inject into sys.modules so the adapter (and any future import of "crewai")
# sees these stubs instead of the real package.
_fake_crewai = types.ModuleType("crewai")
_fake_crewai.Agent = FakeAgent  # type: ignore[attr-defined]
_fake_crewai.Crew = FakeCrew  # type: ignore[attr-defined]
sys.modules["crewai"] = _fake_crewai

# ── Real imports (after stub injection) ───────────────────────────────────────

from panoptis.adapters.crewai import AgentWatchCrewAIAdapter  # noqa: E402
from panoptis.core.event_bus import EventBus  # noqa: E402
from panoptis.core.schema import EventType, ExecutionStatus  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_bus() -> tuple[EventBus, list]:
    """Return a fresh (bus, captured_events) pair for each test."""
    bus = EventBus()
    captured: list = []
    bus.subscribe_fn(lambda event: captured.append(event), handler_id="test.capture")
    return bus, captured


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCrewAIAdapterEventOrder:
    """Verify the lifecycle event sequence emitted during a crew run."""

    def test_full_run_emits_events_in_correct_order(self):
        """
        SESSION_START → TOOL_CALL → TOOL_RESULT → SESSION_END must be
        emitted in that exact order when kickoff() is called.
        """
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(
            session_id="session-abc",
            agent_id="agent-abc",
            event_bus=bus,
        )
        tool = FakeTool("web_search")
        agent = FakeAgent(role="Researcher", tools=[tool])
        crew = FakeCrew(agents=[agent], adapter=adapter)

        crew.kickoff()

        assert [e.event_type for e in captured] == [
            EventType.SESSION_START,
            EventType.TOOL_CALL,
            EventType.TOOL_RESULT,
            EventType.SESSION_END,
        ]

    def test_run_without_tools_emits_start_and_end_only(self):
        """An agent with no tools should still emit SESSION_START + SESSION_END."""
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        crew = FakeCrew(agents=[FakeAgent(role="Writer")], adapter=adapter)

        crew.kickoff()

        types_ = [e.event_type for e in captured]
        assert types_[0] == EventType.SESSION_START
        assert types_[-1] == EventType.SESSION_END
        # No tool events
        assert EventType.TOOL_CALL not in types_
        assert EventType.TOOL_RESULT not in types_


class TestCrewAIAdapterSessionIdentity:
    """All events within one kickoff() must share the same session_id."""

    def test_all_events_share_session_id(self):
        bus, captured = _make_bus()
        session_id = "s-" + uuid.uuid4().hex
        adapter = AgentWatchCrewAIAdapter(session_id=session_id, event_bus=bus)
        crew = FakeCrew(
            agents=[FakeAgent("Researcher", tools=[FakeTool("search")])],
            adapter=adapter,
        )
        crew.kickoff()

        assert len(captured) > 0
        for event in captured:
            assert event.session_id == session_id

    def test_auto_generated_session_id_is_non_empty(self):
        """When no session_id is supplied the adapter must auto-generate one."""
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)  # no session_id
        crew = FakeCrew(agents=[FakeAgent("Writer")], adapter=adapter)
        crew.kickoff()

        assert all(e.session_id for e in captured)  # truthy, non-empty
        # All events share the same auto-generated id
        ids = {e.session_id for e in captured}
        assert len(ids) == 1

    def test_two_independent_runs_have_different_session_ids(self):
        """Two separate adapter instances must not share a session_id."""
        bus, captured = _make_bus()

        for _ in range(2):
            adapter = AgentWatchCrewAIAdapter(event_bus=bus)
            FakeCrew(agents=[FakeAgent("Writer")], adapter=adapter).kickoff()

        session_ids = {e.session_id for e in captured}
        assert len(session_ids) == 2


class TestCrewAIAdapterMetadata:
    """Verify metadata fields on specific event types."""

    def test_session_start_contains_kwargs(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_agent_start(role="Analyst")

        start = captured[0]
        assert start.event_type == EventType.SESSION_START
        assert "role" in start.metadata["kwargs"]

    def test_tool_call_contains_tool_name(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_tool_start("calculator", input="2+2")

        tool_event = captured[0]
        assert tool_event.event_type == EventType.TOOL_CALL
        assert tool_event.metadata["tool_name"] == "calculator"

    def test_tool_result_contains_tool_name_and_result(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_tool_end("calculator", result="4")

        result_event = captured[0]
        assert result_event.event_type == EventType.TOOL_RESULT
        assert result_event.metadata["tool_name"] == "calculator"
        assert result_event.metadata["result"] == "4"

    def test_session_end_contains_result(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_agent_finish(result="final answer")

        end_event = captured[0]
        assert end_event.event_type == EventType.SESSION_END
        assert end_event.metadata["result"] == "final answer"

    def test_session_end_has_success_status(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_agent_finish(result="ok")

        assert captured[0].status == ExecutionStatus.SUCCESS


class TestCrewAIAdapterErrorHandling:
    """Verify error events are emitted correctly."""

    def test_chain_error_emits_agent_error_event(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_chain_error(RuntimeError("something exploded"))

        assert len(captured) == 1
        err = captured[0]
        assert err.event_type == EventType.AGENT_ERROR
        assert err.status == ExecutionStatus.FAILURE
        assert "something exploded" in err.metadata["error"]

    def test_error_does_not_suppress_previous_events(self):
        """Events emitted before an error must still be in the captured list."""
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        adapter.on_agent_start(role="Auditor")
        adapter.on_chain_error(ValueError("oops"))

        types_ = [e.event_type for e in captured]
        assert EventType.SESSION_START in types_
        assert EventType.AGENT_ERROR in types_


class TestCrewAIAdapterStepOrdering:
    """step_number must increase monotonically across a single run."""

    def test_step_numbers_are_sequential(self):
        bus, captured = _make_bus()
        adapter = AgentWatchCrewAIAdapter(event_bus=bus)
        crew = FakeCrew(
            agents=[FakeAgent("Researcher", tools=[FakeTool("search")])],
            adapter=adapter,
        )
        crew.kickoff()

        steps = [e.step_number for e in captured]
        assert steps == list(range(1, len(steps) + 1))
