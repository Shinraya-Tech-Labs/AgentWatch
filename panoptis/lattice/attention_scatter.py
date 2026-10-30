"""Cognitive Lattice — Attention Scatter Detector.

Detects when an agent's attention scatters across unrelated paths or network domains,
indicating loss of focus or potential rogue behaviour.

Uses a sliding window of tool-call steps and a configurable entropy threshold.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from panoptis.core.schema import AgentEvent, EventType

DEFAULT_WINDOW_SIZE = 20
DEFAULT_SCATTER_THRESHOLD = 0.70


def _safe_entropy(items: list[str]) -> float:
    """Compute Shannon entropy for a list of discrete items.

    Returns 0.0 for empty or single-element lists. For N discrete items, compute the
    Shannon entropy and normalise it by the maximum entropy log2(n) to keep output
    within the range [0.0, 1.0].
    """
    n = len(items)
    if n <= 1:
        return 0.0

    frequencies: dict[str, int] = defaultdict(int)
    for item in items:
        frequencies[item] += 1

    entropy = 0.0
    for count in frequencies.values():
        p = count / n
        entropy -= p * math.log2(p)

    max_entropy = math.log2(n) if n > 1 else 1.0
    return entropy / max_entropy if max_entropy > 0 else 0.0


def _extract_parent_dir(path: str) -> str:
    """Return the parent directory of a path, or the path itself if it's a root."""
    parent = path.rsplit("/", 1)[0]
    return parent if parent else "/"


def _domain_from_url(url: str) -> str:
    """Extract domain from a URL. Returns the full string if unparseable."""
    try:
        parts = url.split("://")[1]
        return parts.split("/")[0]
    except (IndexError, AttributeError):
        return url


@dataclass
class AttentionScatterReport:
    """Result of a scatter check for a tool action.

    Attributes:
        blocked: Whether the scatter metric exceeds the safe threshold.
        score: `[0.0, 1.0]` scatter metric (0 = focused, 1 = extreme scatter).
        path_count: Number of unique paths in the window.
        path_entropy: Shannon entropy of the parent directories in the window.
        domain_count: Number of unique domains in the window.
        domain_entropy: Shannon entropy of the domains in the window.
        explanation: Human-readable summary for debugging.
    """

    blocked: bool
    score: float
    path_count: int
    path_entropy: float
    domain_count: int
    domain_entropy: float
    explanation: str

    detected: bool = False
    matches: int = 0
    matched_patterns: list[str] = field(default_factory=list)


@dataclass
class AttentionScatterDetector:
    """Detect attention scatter from tool-call traces.

    Source:
        V2 Cognitive Lattice — observe tool calls and verify the agent is not
        inspecting unrelated files or domains across a region of steps.
    """

    window_size: int = DEFAULT_WINDOW_SIZE
    scatter_threshold: float = DEFAULT_SCATTER_THRESHOLD

    _path_window: deque[str] = field(default_factory=deque)
    _domain_window: deque[str] = field(default_factory=deque)

    def observe(self, event: AgentEvent) -> AttentionScatterReport:
        """Feed one event into the scatter window and return a report."""
        if event.event_type is not EventType.TOOL_CALL or event.tool_call is None:
            return self._empty_safe_report()

        tc = event.tool_call
        affected = tc.affected_resources or []

        paths: list[str] = []
        domains: list[str] = []
        for resource in affected:
            if resource.startswith(("http://", "https://")):
                domains.append(_domain_from_url(resource))
            else:
                paths.append(resource)

        for p in paths:
            self._path_window.append(p)
        for d in domains:
            self._domain_window.append(d)

        return self._compute()

    def _compute(self) -> AttentionScatterReport:
        self._maintain_window_size()
        path_list = list(self._path_window)
        domain_list = list(self._domain_window)
        path_ent = _safe_entropy(path_list) if path_list else 0.0
        dom_ent = _safe_entropy(domain_list) if domain_list else 0.0
        score = compute_scatter_score(
            self._compute_path_entropy(),
            self._compute_domain_entropy(),
        )
        blocked = score > self.scatter_threshold

        explanation: list[str] = []
        if len(path_list) >= 3:
            explanation.append(
                f"{len(path_list)} paths across {len(set(path_list))} directories (entropy {path_ent:.2f})"
            )
        if len(domain_list) >= 3:
            explanation.append(f"{len(domain_list)} domains (entropy {dom_ent:.2f})")
        if blocked:
            explanation.insert(0, "BLOCKED: scatter exceeds threshold")
        elif score >= self.scatter_threshold * 0.5:
            explanation.insert(0, "WARN: scatter is elevated")

        return AttentionScatterReport(
            blocked=blocked,
            matches=len(path_list) + len(domain_list),
            matched_patterns=path_list + domain_list,
            score=score,
            path_count=len(set(path_list)),
            path_entropy=path_ent,
            domain_count=len(set(domain_list)),
            domain_entropy=dom_ent,
            explanation=" | ".join(explanation) if explanation else "",
        )

    def reset(self):
        self._path_window.clear()
        self._domain_window.clear()

    def _empty_safe_report(self) -> AttentionScatterReport:
        return AttentionScatterReport(
            blocked=False,
            score=0.0,
            path_count=0,
            path_entropy=0.0,
            domain_count=0,
            domain_entropy=0.0,
            explanation="",
        )

    def _maintain_window_size(self) -> None:
        while len(self._path_window) > self.window_size:
            self._path_window.popleft()
        while len(self._domain_window) > self.window_size:
            self._domain_window.popleft()

    def _compute_path_entropy(self) -> float:
        return _safe_entropy([_extract_parent_dir(p) for p in self._path_window])

    def _compute_domain_entropy(self) -> float:
        return _safe_entropy(list(self._domain_window))


def compute_scatter_score(path_entropy: float, domain_entropy: float) -> float:
    """Compute combined scatter score (0.0..1.0)."""
    return max(path_entropy, domain_entropy)


__all__ = [
    "AttentionScatterDetector",
    "AttentionScatterReport",
    "compute_scatter_score",
]
