"""
MAG-005 — Shared Crew Context.

All agents in a crew share one session context. Agent-to-agent calls create
edges in the shared graph. The crew is observable as a unified system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentMessageData,
    EventType,
)
from panoptis.orchestration.dag import InterAgentDAG


@dataclass
class CrewMember:
    agent_id: str
    role: str
    metadata: dict[str, Any] = field(default_factory=dict)


class CrewContext:
    """Shared context for a crew of agents."""

    def __init__(
        self,
        crew_id: str | None = None,
        event_bus: EventBus | None = None,
    ):
        self.crew_id = crew_id or f"crew-{uuid.uuid4().hex[:8]}"
        self.session_id = f"{self.crew_id}-session"
        self.members: dict[str, CrewMember] = {}
        self.dag = InterAgentDAG()
        self.bus = event_bus or get_event_bus()
        self._step = 0

    def register(self, agent_id: str, role: str, **meta: Any) -> CrewMember:
        m = CrewMember(agent_id=agent_id, role=role, metadata=meta)
        self.members[agent_id] = m
        return m

    def record_call(
        self,
        from_agent: str,
        to_agent: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        self._step += 1
        node_id = f"{self.crew_id}-{self._step}"
        self.dag.add_node(node_id, agent_id=from_agent, action=action, metadata=payload or {})
        # Connect this call to the most recent node from the target (if any)
        # for downstream propagation tracing.
        prior = [n for n in self.dag.nodes.values() if n.agent_id == to_agent]
        if prior:
            try:
                self.dag.add_edge(node_id, prior[-1].node_id, kind="delegate")
            except ValueError:
                # would form cycle — skip
                pass

        event = AgentEvent(
            session_id=self.session_id,
            agent_id=from_agent,
            event_type=EventType.AGENT_MESSAGE,
            agent_message=AgentMessageData(
                sender_agent_id=from_agent,
                receiver_agent_id=to_agent,
                message_type="task",
                content=payload or {},
            ),
            metadata={"crew_id": self.crew_id, "node_id": node_id},
        )
        self.bus.publish_sync(event)
        return node_id


__all__ = ["CrewContext", "CrewMember"]
