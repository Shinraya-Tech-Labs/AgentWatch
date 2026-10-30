"""
AgentWatch Multi-Agent Orchestration Engine
Native coordination of specialized subagents with typed task graphs,
structured messaging, shared memory bus, and replayable coordination.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from panoptis.core.event_bus import EventBus, get_event_bus
from panoptis.core.schema import (
    AgentEvent,
    AgentFramework,
    AgentMessageData,
    EventType,
    ExecutionStatus,
    TaskNode,
)

logger = logging.getLogger(__name__)


class AgentRole(str, Enum):
    PLANNER = "planner"  # Decomposes goals into tasks
    EXECUTOR = "executor"  # Executes concrete actions
    VERIFIER = "verifier"  # Validates outputs
    MEMORY = "memory"  # Manages retrieval and context
    COORDINATOR = "coordinator"  # Routes and delegates


class MessageType(str, Enum):
    TASK_ASSIGN = "task_assign"
    TASK_RESULT = "task_result"
    TASK_FAIL = "task_fail"
    QUERY = "query"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    HEARTBEAT = "heartbeat"


@dataclass
class AgentMessage:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender_id: str = ""
    receiver_id: str = ""
    message_type: MessageType = MessageType.QUERY
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    task_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = 300

    @property
    def is_expired(self) -> bool:
        age = (datetime.now(UTC) - self.timestamp).total_seconds()
        return age > self.ttl_seconds


@dataclass
class SubAgent:
    agent_id: str
    name: str
    role: AgentRole
    framework: AgentFramework
    capabilities: list[str] = field(default_factory=list)
    max_concurrent_tasks: int = 1
    _active_tasks: int = field(default=0, init=False, repr=False)
    _inbox: asyncio.Queue = field(default_factory=asyncio.Queue, init=False, repr=False)
    _handler: Callable | None = field(default=None, init=False, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self._active_tasks < self.max_concurrent_tasks

    def set_handler(self, fn: Callable[[AgentMessage], Any]) -> None:
        self._handler = fn

    async def send_message(self, msg: AgentMessage) -> None:
        await self._inbox.put(msg)

    async def process_inbox(self) -> None:
        """Run the agent's message processing loop."""
        while True:
            msg = await self._inbox.get()
            if msg.is_expired:
                logger.debug("Dropped expired message for agent %s", self.agent_id)
                continue
            if self._handler:
                try:
                    self._active_tasks += 1
                    await self._handler(msg)
                except Exception as exc:
                    logger.error("Agent %s handler error: %s", self.agent_id, exc)
                finally:
                    self._active_tasks -= 1


class TaskGraph:
    """Directed acyclic graph of tasks with dependency tracking."""

    def __init__(self, session_id: str, goal: str):
        self.graph_id = str(uuid.uuid4())
        self.session_id = session_id
        self.goal = goal
        self.nodes: dict[str, TaskNode] = {}
        self._adjacency: dict[str, set[str]] = {}
        self.created_at = datetime.now(UTC)

    def add_task(
        self,
        title: str,
        description: str | None = None,
        depends_on: list[str] | None = None,
        assigned_agent_id: str | None = None,
    ) -> TaskNode:
        node = TaskNode(
            session_id=self.session_id,
            title=title,
            description=description,
            dependencies=depends_on or [],
            assigned_agent_id=assigned_agent_id,
        )
        self.nodes[node.task_id] = node
        self._adjacency[node.task_id] = set()

        for dep_id in node.dependencies:
            if dep_id not in self._adjacency:
                self._adjacency[dep_id] = set()
            self._adjacency[dep_id].add(node.task_id)

        return node

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose all dependencies are completed."""
        ready = []
        for node in self.nodes.values():
            if node.status != ExecutionStatus.PENDING:
                continue
            deps_met = all(
                self.nodes.get(dep_id, None) is not None
                and self.nodes[dep_id].status == ExecutionStatus.SUCCESS
                for dep_id in node.dependencies
            )
            if deps_met:
                ready.append(node)
        return ready

    def mark_started(self, task_id: str, agent_id: str) -> None:
        node = self.nodes.get(task_id)
        if node:
            node.status = ExecutionStatus.RUNNING
            node.started_at = datetime.now(UTC)
            node.assigned_agent_id = agent_id

    def mark_completed(self, task_id: str, outputs: dict[str, Any] | None = None) -> None:
        node = self.nodes.get(task_id)
        if node:
            node.status = ExecutionStatus.SUCCESS
            node.completed_at = datetime.now(UTC)
            if outputs:
                node.outputs = outputs

    def mark_failed(self, task_id: str, error: str = "") -> None:
        node = self.nodes.get(task_id)
        if node:
            node.status = ExecutionStatus.FAILURE
            node.completed_at = datetime.now(UTC)
            node.metadata["error"] = error

    @property
    def is_complete(self) -> bool:
        return all(n.status == ExecutionStatus.SUCCESS for n in self.nodes.values())

    @property
    def has_failures(self) -> bool:
        return any(n.status == ExecutionStatus.FAILURE for n in self.nodes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "total_tasks": len(self.nodes),
            "completed": sum(1 for n in self.nodes.values() if n.status == ExecutionStatus.SUCCESS),
            "failed": sum(1 for n in self.nodes.values() if n.status == ExecutionStatus.FAILURE),
            "running": sum(1 for n in self.nodes.values() if n.status == ExecutionStatus.RUNNING),
            "pending": sum(1 for n in self.nodes.values() if n.status == ExecutionStatus.PENDING),
            "nodes": [n.model_dump(mode="json") for n in self.nodes.values()],
        }


class SharedMemoryBus:
    """
    Lightweight in-process shared memory between agents.
    For production: back with Redis.
    """

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = value
            queues = self._subscribers.get(key, [])
            for q in queues:
                await q.put((key, value))

    async def get(self, key: str) -> Any | None:
        return self._store.get(key)

    async def subscribe(self, key: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            if key not in self._subscribers:
                self._subscribers[key] = []
            self._subscribers[key].append(q)
        return q

    def snapshot(self) -> dict[str, Any]:
        return dict(self._store)


class OrchestrationEngine:
    """
    Coordinates multiple subagents to complete a goal.
    """

    def __init__(
        self,
        session_id: str | None = None,
        event_bus: EventBus | None = None,
    ):
        self.session_id = session_id or str(uuid.uuid4())
        self._bus = event_bus or get_event_bus()
        self._agents: dict[str, SubAgent] = {}
        self._agent_tasks: dict[str, asyncio.Task] = {}
        self._shared_memory = SharedMemoryBus()
        self._message_log: list[AgentMessage] = []
        self._active_graph: TaskGraph | None = None
        self._dispatch_lock = asyncio.Lock()

    def register_agent(self, agent: SubAgent) -> None:
        self._agents[agent.agent_id] = agent
        logger.info("Registered agent %s (%s/%s)", agent.agent_id, agent.role.value, agent.name)

    def get_agent(self, agent_id: str) -> SubAgent | None:
        return self._agents.get(agent_id)

    def agents_by_role(self, role: AgentRole) -> list[SubAgent]:
        return [a for a in self._agents.values() if a.role == role]

    async def start(self) -> None:
        """Start all agent processing loops."""
        for agent in self._agents.values():
            task = asyncio.create_task(agent.process_inbox(), name=f"agent-{agent.agent_id}")
            self._agent_tasks[agent.agent_id] = task
        logger.info("Orchestration engine started with %d agents", len(self._agents))

    async def stop(self) -> None:
        for task in self._agent_tasks.values():
            task.cancel()
        await asyncio.gather(*self._agent_tasks.values(), return_exceptions=True)
        logger.info("Orchestration engine stopped")

    async def send_message(
        self,
        sender_id: str,
        receiver_id: str,
        message_type: MessageType,
        payload: dict[str, Any],
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentMessage:
        msg = AgentMessage(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message_type=message_type,
            payload=payload,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        self._message_log.append(msg)

        event = AgentEvent(
            session_id=self.session_id,
            agent_id=sender_id,
            framework=AgentFramework.CUSTOM,
            event_type=EventType.AGENT_MESSAGE,
            agent_message=AgentMessageData(
                sender_agent_id=sender_id,
                receiver_agent_id=receiver_id,
                message_type=message_type.value,
                content=payload,
                correlation_id=correlation_id,
            ),
        )
        await self._bus.publish(event)

        receiver = self._agents.get(receiver_id)
        if receiver:
            await receiver.send_message(msg)
        else:
            logger.warning("Message to unknown agent %s", receiver_id)

        return msg

    async def delegate_task(
        self,
        task: TaskNode,
        from_agent_id: str = "orchestrator",
    ) -> bool:
        """Delegate a task to an available executor agent."""
        async with self._dispatch_lock:
            executors = [a for a in self.agents_by_role(AgentRole.EXECUTOR) if a.is_available]
            if not executors:
                logger.warning("No available executor agents for task %s", task.task_id)
                return False

            executor = executors[0]

            await self.send_message(
                sender_id=from_agent_id,
                receiver_id=executor.agent_id,
                message_type=MessageType.TASK_ASSIGN,
                payload={
                    "task_id": task.task_id,
                    "title": task.title,
                    "description": task.description,
                },
                task_id=task.task_id,
            )

            if self._active_graph:
                self._active_graph.mark_started(task.task_id, executor.agent_id)

            event = AgentEvent(
                session_id=self.session_id,
                agent_id=from_agent_id,
                framework=AgentFramework.CUSTOM,
                event_type=EventType.TASK_DELEGATE,
                task_id=task.task_id,
                metadata={"executor_id": executor.agent_id, "task_title": task.title},
            )
            await self._bus.publish(event)
            return True

    async def run_graph(self, graph: TaskGraph) -> dict[str, Any]:
        """
        Execute a task graph by dispatching ready tasks
        to available agents until completion or failure.
        """
        self._active_graph = graph
        max_iterations = len(graph.nodes) * 3
        iteration = 0

        while not graph.is_complete and not graph.has_failures:
            ready = graph.get_ready_tasks()
            if not ready:
                if all(
                    n.status in (ExecutionStatus.RUNNING, ExecutionStatus.SUCCESS)
                    for n in graph.nodes.values()
                ):
                    await asyncio.sleep(0.1)
                    continue
                else:
                    break

            for task in ready:
                await self.delegate_task(task)

            await asyncio.sleep(0.05)
            iteration += 1
            if iteration >= max_iterations:
                logger.warning("Task graph reached max iterations — possible deadlock")
                break

        return graph.to_dict()

    def message_log(self) -> list[dict[str, Any]]:
        return [
            {
                "message_id": m.message_id,
                "sender": m.sender_id,
                "receiver": m.receiver_id,
                "type": m.message_type.value,
                "task_id": m.task_id,
                "timestamp": m.timestamp.isoformat(),
            }
            for m in self._message_log
        ]

    @property
    def shared_memory(self) -> SharedMemoryBus:
        return self._shared_memory

    def agent_status(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": a.agent_id,
                "name": a.name,
                "role": a.role.value,
                "active_tasks": a._active_tasks,
                "available": a.is_available,
                "capabilities": a.capabilities,
            }
            for a in self._agents.values()
        ]
