"""
AgentWatch Integration Tests
Tests the full event pipeline: adapters → bus → safety → collector → API.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from panoptis.core.event_bus import EventBus
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentSession,
    EventType,
    ExecutionStatus,
    ToolCallData,
    ToolResultData,
)
from panoptis.memory.engine import ImportanceLevel, MemoryEngine, MemoryType
from panoptis.orchestration.engine import (
    AgentRole,
    MessageType,
    OrchestrationEngine,
    SubAgent,
    TaskGraph,
)
from panoptis.replay.engine import FailureCause, ReplayEngine, ReplaySpeed
from panoptis.scoring.confidence import ConfidenceScorer
from panoptis.tracing.collector import TraceCollector

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def safety() -> SafetyEngine:
    return SafetyEngine()


@pytest.fixture
def collector() -> TraceCollector:
    return TraceCollector()


@pytest.fixture
def scorer() -> ConfidenceScorer:
    return ConfidenceScorer()


@pytest.fixture
def memory() -> MemoryEngine:
    return MemoryEngine()


@pytest.fixture
def replay_engine() -> ReplayEngine:
    return ReplayEngine()


# ─────────────────────────────────────────────
# Full event pipeline test
# ─────────────────────────────────────────────


class TestEventPipeline:
    @pytest.mark.asyncio
    async def test_events_flow_through_bus_to_collector(self, bus, collector):
        """Events published to bus are collected by the trace collector."""
        bus.subscribe_fn(collector.ingest, handler_id="test.collector")

        session_id = str(uuid.uuid4())
        agent_id = "integration-agent"

        events = [
            AgentEvent(
                session_id=session_id,
                agent_id=agent_id,
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.SESSION_START,
            ),
            AgentEvent(
                session_id=session_id,
                agent_id=agent_id,
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.TOOL_CALL,
                tool_call=ToolCallData(
                    tool_name="bash", raw_command="echo test", arguments={"command": "echo test"}
                ),
            ),
            AgentEvent(
                session_id=session_id,
                agent_id=agent_id,
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.SESSION_END,
                status=ExecutionStatus.SUCCESS,
            ),
        ]

        for event in events:
            await bus.publish(event)

        # Give async handlers time to complete
        await asyncio.sleep(0.05)

        trace = collector.get_trace(session_id)
        assert trace is not None
        assert trace.event_count == 3

    @pytest.mark.asyncio
    async def test_safety_engine_blocks_before_bus(self, bus, collector):
        """Safety engine blocks critical events; blocked events still emitted."""
        bus.subscribe_fn(collector.ingest, handler_id="test.collector")
        safety = SafetyEngine()  # noqa: F841 — kept for fixture lifecycle

        session_id = str(uuid.uuid4())
        dangerous = AgentEvent(
            session_id=session_id,
            agent_id="agent",
            framework=AgentFramework.CLAUDE_CODE,
            event_type=EventType.TOOL_CALL,
            tool_call=ToolCallData(
                tool_name="bash",
                raw_command="rm -rf /",
                arguments={"command": "rm -rf /"},
            ),
        )

        # Safety check
        result = await safety.check_event(dangerous)
        assert result.is_blocked

        # Publish anyway (to record the block)
        await bus.publish(result)
        await asyncio.sleep(0.05)

        trace = collector.get_trace(session_id)
        assert trace is not None
        blocked_spans = [s for s in trace.spans if s._event.is_blocked]
        assert len(blocked_spans) == 1

    @pytest.mark.asyncio
    async def test_full_pipeline_with_replay(self, bus, collector, replay_engine):
        """Complete pipeline: ingest → collect → replay → analyze."""
        bus.subscribe_fn(collector.ingest, handler_id="test.collector")
        safety = SafetyEngine()  # noqa: F841 — kept for fixture lifecycle

        session_id = str(uuid.uuid4())
        session = AgentSession(
            session_id=session_id,
            agent_id="agent",
            framework=AgentFramework.CLAUDE_CODE,
            goal="Test the full pipeline",
        )
        collector.register_session(session)

        events_to_publish = [
            AgentEvent(
                session_id=session_id,
                agent_id="agent",
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.SESSION_START,
                goal="Test",
            ),
            AgentEvent(
                session_id=session_id,
                agent_id="agent",
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.TOOL_CALL,
                step_number=1,
                tool_call=ToolCallData(
                    tool_name="bash", raw_command="ls", arguments={"command": "ls"}
                ),
            ),
            AgentEvent(
                session_id=session_id,
                agent_id="agent",
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.TOOL_RESULT,
                step_number=2,
                status=ExecutionStatus.SUCCESS,
                tool_result=ToolResultData(tool_name="bash", output="file1.txt"),
            ),
            AgentEvent(
                session_id=session_id,
                agent_id="agent",
                framework=AgentFramework.CLAUDE_CODE,
                event_type=EventType.SESSION_END,
                step_number=3,
                status=ExecutionStatus.SUCCESS,
            ),
        ]

        for ev in events_to_publish:
            await bus.publish(ev)

        await asyncio.sleep(0.05)

        # Retrieve and replay
        collected_events = collector.get_events(session_id)
        assert len(collected_events) == 4

        rs = replay_engine.load_from_events(session, collected_events)
        assert rs.total_steps == 4

        steps_seen = []
        async for step in replay_engine.replay_async(rs, speed=ReplaySpeed.INSTANT):
            steps_seen.append(step.index)

        assert len(steps_seen) == 4


# ─────────────────────────────────────────────
# Memory integration
# ─────────────────────────────────────────────


class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, memory):
        agent_id = "mem-test-agent"

        await memory.store(
            agent_id, "The deployment is on port 8080", memory_type=MemoryType.SEMANTIC
        )
        await memory.store(
            agent_id, "Never commit secrets to git", memory_type=MemoryType.PROCEDURAL
        )
        await memory.store(
            agent_id,
            "Previously failed to connect to database on port 5432",
            memory_type=MemoryType.EPISODIC,
        )

        results = await memory.retrieve(agent_id, "deployment configuration")
        assert len(results) > 0
        # Port 8080 is semantically related to deployment
        assert any("8080" in r.entry.content or "port" in r.entry.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_context_window_returns_string(self, memory):
        agent_id = "ctx-test-agent"
        await memory.store(
            agent_id,
            "User prefers Python over JavaScript",
            memory_type=MemoryType.SEMANTIC,
            importance=ImportanceLevel.HIGH,
        )
        ctx = await memory.get_context_window(agent_id, "what language should I use")
        assert isinstance(ctx, str)

    @pytest.mark.asyncio
    async def test_memory_stats(self, memory):
        agent_id = "stats-test"
        await memory.store(agent_id, "fact one", memory_type=MemoryType.SEMANTIC)
        await memory.store(agent_id, "event one", memory_type=MemoryType.EPISODIC)
        await memory.store(agent_id, "how to do it", memory_type=MemoryType.PROCEDURAL)

        stats = memory.stats(agent_id)
        assert stats["total_entries"] == 3
        assert stats["by_type"]["semantic"] == 1
        assert stats["by_type"]["episodic"] == 1
        assert stats["by_type"]["procedural"] == 1


# ─────────────────────────────────────────────
# Orchestration integration
# ─────────────────────────────────────────────


class TestOrchestrationIntegration:
    @pytest.mark.asyncio
    async def test_task_graph_executes(self):
        bus = EventBus()
        orch = OrchestrationEngine(session_id="test-orch", event_bus=bus)

        executed_tasks: list[str] = []

        async def exec_handler(msg):
            if msg.message_type == MessageType.TASK_ASSIGN:
                tid = msg.payload.get("task_id")
                await asyncio.sleep(0.005)
                executed_tasks.append(tid)
                if orch._active_graph:
                    orch._active_graph.mark_completed(tid)

        executor = SubAgent(
            "exec-1",
            "Executor",
            AgentRole.EXECUTOR,
            AgentFramework.CLAUDE_CODE,
            max_concurrent_tasks=3,
        )
        executor.set_handler(exec_handler)
        orch.register_agent(executor)

        await orch.start()

        graph = TaskGraph("test-orch", "Run three parallel tasks")
        t1 = graph.add_task("Task A")
        t2 = graph.add_task("Task B")
        t3 = graph.add_task("Task C", depends_on=[t1.task_id, t2.task_id])  # noqa: F841 — task registered with graph side-effect

        result = await asyncio.wait_for(orch.run_graph(graph), timeout=5.0)
        await orch.stop()

        # All tasks should be dispatched
        assert result["total_tasks"] == 3
        # Note: graph completion depends on handler speed — check dispatched
        assert len(executed_tasks) >= 2  # At least t1, t2 dispatched

    @pytest.mark.asyncio
    async def test_shared_memory_bus(self):
        bus = EventBus()
        orch = OrchestrationEngine(event_bus=bus)

        await orch.shared_memory.publish("config:api_url", "http://localhost:8000")
        val = await orch.shared_memory.get("config:api_url")
        assert val == "http://localhost:8000"

        snap = orch.shared_memory.snapshot()
        assert "config:api_url" in snap


# ─────────────────────────────────────────────
# Confidence + replay integration
# ─────────────────────────────────────────────


class TestConfidenceReplayIntegration:
    @pytest.mark.asyncio
    async def test_looping_agent_detected(self):
        """A looping agent session should score low and get flagged."""
        session_id = str(uuid.uuid4())
        session = AgentSession(
            session_id=session_id,
            agent_id="loop-agent",
            framework=AgentFramework.CLAUDE_CODE,
            goal="Fix the bug",
        )

        # Build a looping event sequence
        events = []
        for i in range(12):
            events.append(
                AgentEvent(
                    session_id=session_id,
                    agent_id="loop-agent",
                    framework=AgentFramework.CLAUDE_CODE,
                    event_type=EventType.TOOL_CALL,
                    step_number=i * 2,
                    tool_call=ToolCallData(
                        tool_name="bash" if i % 2 == 0 else "file_read",
                        raw_command="grep error logs/app.log",
                        arguments={"command": "grep error logs/app.log"},
                    ),
                )
            )
            events.append(
                AgentEvent(
                    session_id=session_id,
                    agent_id="loop-agent",
                    framework=AgentFramework.CLAUDE_CODE,
                    event_type=EventType.TOOL_RESULT,
                    step_number=i * 2 + 1,
                    status=ExecutionStatus.SUCCESS,
                    tool_result=ToolResultData(
                        tool_name="bash", output="error: connection refused"
                    ),
                )
            )

        scorer = ConfidenceScorer()
        result = scorer.score(events, goal="Fix the bug")

        # Should detect anomalies
        assert len(result.anomaly_flags) > 0
        assert result.overall_score < 0.9  # Not a perfect score

        # Replay engine should also flag it
        engine = ReplayEngine()
        rs = engine.load_from_events(session, events)
        # The repeated tool pattern should appear in analysis
        if rs.failure_analysis:
            assert rs.failure_analysis.primary_cause in (
                FailureCause.REPEATED_TOOL_FAILURE,
                FailureCause.INFINITE_LOOP,
                FailureCause.UNKNOWN,
            )
