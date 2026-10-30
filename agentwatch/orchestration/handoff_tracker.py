"""Directed handoff cycle and task-delegation deadlock detector."""

from __future__ import annotations

from dataclasses import dataclass, field

ALERT_AGENT_DELEGATION_DEADLOCK = "AGENT_DELEGATION_DEADLOCK"


@dataclass(frozen=True)
class HandoffDeadlockReport:
    """Diagnostic report emitted when directed handoff cycles are evaluated."""

    deadlock_detected: bool
    cycle: list[str] = field(default_factory=list)
    cycle_str: str = ""
    active_tasks: list[str] = field(default_factory=list)
    alert_code: str | None = None


class HandoffGraphTracker:
    """Maintains active task delegation graph and detects cyclical deadlocks using 3-color DFS."""

    WHITE, GRAY, BLACK = 0, 1, 2

    def __init__(self) -> None:
        self._adj: dict[str, list[tuple[str, str]]] = {}
        self._resolved_tasks: set[str] = set()

    def record_delegation(self, src: str, tgt: str, task_id: str = "") -> HandoffDeadlockReport:
        self._adj.setdefault(src, []).append((tgt, task_id))
        return self.detect_cycles()

    def record_progress(self, task_id: str) -> None:
        if task_id:
            self._resolved_tasks.add(task_id)

    def resolve_delegation(self, src: str, tgt: str, task_id: str = "") -> None:
        if src in self._adj:
            self._adj[src] = [
                (t, i)
                for t, i in self._adj[src]
                if not (t == tgt and (not task_id or i == task_id))
            ]
            if not self._adj[src]:
                self._adj.pop(src, None)

    def clear(self) -> None:
        self._adj.clear()
        self._resolved_tasks.clear()

    def detect_cycles(self) -> HandoffDeadlockReport:
        graph: dict[str, list[tuple[str, str]]] = {}
        nodes: set[str] = set()
        for src, edges in self._adj.items():
            active = [(t, i) for t, i in edges if not i or i not in self._resolved_tasks]
            if active:
                graph[src] = active
                nodes.update([src, *(t for t, _ in active)])

        color = {node: self.WHITE for node in nodes}
        path: list[str] = []
        cycle_found: list[str] = []

        def dfs(u: str) -> bool:
            color[u] = self.GRAY
            path.append(u)
            for v, _ in graph.get(u, []):
                if color.get(v, self.WHITE) == self.GRAY:
                    cycle = path[path.index(v) :] + [v]
                    if len(cycle) >= 3:
                        cycle_found.extend(cycle)
                        return True
                elif color.get(v, self.WHITE) == self.WHITE and dfs(v):
                    return True
            path.pop()
            color[u] = self.BLACK
            return False

        for node in sorted(nodes):
            if color[node] == self.WHITE and dfs(node):
                tasks = [i for s in cycle_found[:-1] for _, i in graph.get(s, []) if i]
                return HandoffDeadlockReport(
                    deadlock_detected=True,
                    cycle=cycle_found,
                    cycle_str=" -> ".join(cycle_found),
                    active_tasks=tasks,
                    alert_code=ALERT_AGENT_DELEGATION_DEADLOCK,
                )

        return HandoffDeadlockReport(deadlock_detected=False)
