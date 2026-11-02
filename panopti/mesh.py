"""Panopti Mesh: Multi-Agent Swarm Topology Tracing and Causal Attribution."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("panopti.mesh")


@dataclass
class SwarmAgentNode:
    """Node in the multi-agent swarm hierarchy."""

    agent_id: str
    role: str
    parent_id: str | None = None
    status: str = "active"  # active, completed, failed
    task_description: str = ""
    failure_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list[str] = field(default_factory=list)


class SwarmMesh:
    """Maintains the dynamic causal delegation graph across a multi-agent swarm."""

    def __init__(self, root_agent_id: str = "supervisor", root_role: str = "supervisor") -> None:
        self._nodes: dict[str, SwarmAgentNode] = {}
        self.root_agent_id = root_agent_id
        self.register_agent(root_agent_id, role=root_role, parent_id=None)

    def register_agent(
        self,
        agent_id: str,
        role: str = "worker",
        parent_id: str | None = None,
        task_description: str = "",
    ) -> SwarmAgentNode:
        """Register an agent or subagent within the swarm mesh."""
        effective_parent = (
            parent_id
            if parent_id is not None
            else (self.root_agent_id if agent_id != self.root_agent_id else None)
        )
        node = SwarmAgentNode(
            agent_id=agent_id,
            role=role,
            parent_id=effective_parent,
            task_description=task_description,
        )
        self._nodes[agent_id] = node

        if effective_parent and effective_parent in self._nodes:
            if agent_id not in self._nodes[effective_parent].children:
                self._nodes[effective_parent].children.append(agent_id)

        return node

    def record_outcome(
        self,
        agent_id: str,
        success: bool,
        reason: str | None = None,
    ) -> None:
        """Record task completion or failure for an agent node."""
        if agent_id in self._nodes:
            node = self._nodes[agent_id]
            node.status = "completed" if success else "failed"
            node.failure_reason = reason if not success else None

    def attribute_root_cause(self, failed_agent_id: str) -> list[SwarmAgentNode]:
        """Traverse the causal chain upward from a failed agent to identify root delegation lineage.

        Returns:
            List of SwarmAgentNode from root ancestor down to the failed node.
        """
        lineage: list[SwarmAgentNode] = []
        curr_id: str | None = failed_agent_id

        while curr_id and curr_id in self._nodes:
            node = self._nodes[curr_id]
            lineage.append(node)
            curr_id = node.parent_id

        lineage.reverse()
        return lineage

    def render_ascii_dag(self) -> str:
        """Render an aesthetic ASCII tree of the active swarm topology."""
        lines: list[str] = [f"🌐 Panopti Swarm Mesh (Root: {self.root_agent_id})"]

        def _walk(node_id: str, prefix: str, is_tail: bool) -> None:
            node = self._nodes.get(node_id)
            if not node:
                return

            status_icon = (
                "🟢" if node.status == "completed" else ("🔴" if node.status == "failed" else "🟡")
            )
            desc = f" - '{node.task_description[:30]}...'" if node.task_description else ""
            connector = "└── " if is_tail else "├── "
            lines.append(
                f"{prefix}{connector}{status_icon} [{node.role}] {node.agent_id} ({node.status}){desc}"
            )

            new_prefix = prefix + ("    " if is_tail else "│   ")
            child_count = len(node.children)
            for i, child_id in enumerate(node.children):
                _walk(child_id, new_prefix, i == (child_count - 1))

        if self.root_agent_id in self._nodes:
            root = self._nodes[self.root_agent_id]
            status_icon = (
                "🟢" if root.status == "completed" else ("🔴" if root.status == "failed" else "🟡")
            )
            lines.append(f"{status_icon} [{root.role}] {root.agent_id} ({root.status})")
            for i, child_id in enumerate(root.children):
                _walk(child_id, "  ", i == (len(root.children) - 1))

        return "\n".join(lines)
