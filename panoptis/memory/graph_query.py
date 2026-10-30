"""
MEM-008 — Natural Language Causal-Graph Traversal.

A natural-language query interface for the causal memory graph. Parses a
plain-English question (e.g. "Why did we choose Postgres over MySQL in the
previous session?") and compiles it into a *graph traversal* over the
:class:`~panoptis.memory.causal_graph.CausalGraph` — following causal edges
between nodes — rather than the flat key/value lookup performed by
:mod:`panoptis.memory.nlquery` (MEM-007).

The distinction matters: MEM-007 ranks independent memory rows by keyword and
embedding similarity. This module instead picks an *entry node* in the causal
graph and then walks the graph's edges (upstream for "why" questions,
downstream for "what happened next" questions) to surface the causal *path*
that answers the question.

Resolution is deterministic and dependency-free: it reuses the hashed-vector
``embed``/``cosine`` helpers from :mod:`panoptis.scoring.drift` (which fall
back to a stable hashing embedding when sentence-transformers is unavailable),
with a keyword-overlap signal blended in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from panoptis.memory.causal_graph import CausalGraph, CausalNode, EdgeKind
from panoptis.scoring.drift import cosine, embed


class TraversalDirection(str, Enum):
    """Which way to walk the causal graph from the entry node."""

    UPSTREAM = "upstream"  # causes / constraints that led to a node ("why")
    DOWNSTREAM = "downstream"  # outcomes / effects a node produced ("what happened")


# Words that signal the user is asking for *causes* — walk upstream.
_UPSTREAM_CUES = (
    "why",
    "what caused",
    "what led to",
    "reason",
    "because",
    "motivat",  # motivated / motivation
    "justif",  # justify / justification
    "rationale",
    "drove",
    "led us",
)

# Words that signal the user is asking for *effects* — walk downstream.
_DOWNSTREAM_CUES = (
    "what happened",
    "what resulted",
    "result of",
    "outcome",
    "effect",
    "impact",
    "led to",
    "consequence",
    "what did it produce",
    "what came",
)

# Maps a word found in the question to an EdgeKind, so a question can optionally
# be restricted to one relationship type.
_EDGE_CUES: dict[str, EdgeKind] = {
    "constraint": EdgeKind.CONSTRAINED_BY,
    "constrain": EdgeKind.CONSTRAINED_BY,
    "limit": EdgeKind.CONSTRAINED_BY,
    "cause": EdgeKind.CAUSED_BY,
    "context": EdgeKind.CAUSED_BY,
    "produce": EdgeKind.PRODUCED,
    "outcome": EdgeKind.PRODUCED,
    "result": EdgeKind.PRODUCED,
    "support": EdgeKind.SUPPORTS,
    "contradict": EdgeKind.CONTRADICTS,
    "conflict": EdgeKind.CONTRADICTS,
}

_STOPWORDS = {
    "why",
    "what",
    "when",
    "where",
    "which",
    "who",
    "how",
    "did",
    "does",
    "do",
    "the",
    "a",
    "an",
    "we",
    "us",
    "our",
    "i",
    "you",
    "they",
    "it",
    "in",
    "on",
    "of",
    "for",
    "to",
    "from",
    "over",
    "with",
    "and",
    "or",
    "was",
    "were",
    "is",
    "are",
    "be",
    "been",
    "choose",
    "chose",
    "chosen",
    "decide",
    "decided",
    "previous",
    "session",
    "last",
    "happened",
    "result",
    "about",
    "that",
    "this",
    "led",
    "reason",
    "because",
}


@dataclass
class GraphQuery:
    """A parsed natural-language question, ready to compile into a traversal."""

    raw: str
    direction: TraversalDirection
    keywords: list[str] = field(default_factory=list)
    edge_filter: EdgeKind | None = None
    max_depth: int = 4


@dataclass
class TraversalStep:
    """One node on the answer path, with how it relates to the entry node."""

    node: CausalNode
    depth: int

    def to_dict(self) -> dict:
        return {
            "node_id": self.node.node_id,
            "kind": self.node.kind,
            "text": self.node.text,
            "depth": self.depth,
            "timestamp": self.node.timestamp.isoformat(),
        }


@dataclass
class GraphQueryResult:
    """The compiled traversal result for a natural-language question."""

    question: str
    direction: TraversalDirection
    entry_node: CausalNode | None
    match_score: float
    path: list[TraversalStep] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        """True when an entry node was found and it has a causal path."""
        return self.entry_node is not None and len(self.path) > 0

    def summary(self) -> str:
        """A short human-readable rendering of the causal path."""
        if self.entry_node is None:
            return "No matching memory node found for this question."
        arrow = "←" if self.direction is TraversalDirection.UPSTREAM else "→"
        parts = [self.entry_node.text]
        for step in self.path:
            parts.append(f"{arrow} {step.node.text}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "direction": self.direction.value,
            "entry_node": (
                {
                    "node_id": self.entry_node.node_id,
                    "kind": self.entry_node.kind,
                    "text": self.entry_node.text,
                }
                if self.entry_node
                else None
            ),
            "match_score": self.match_score,
            "answered": self.answered,
            "path": [step.to_dict() for step in self.path],
            "summary": self.summary(),
        }


def _detect_direction(question: str) -> TraversalDirection:
    """Infer whether the question asks for causes (upstream) or effects."""
    q = question.lower()
    # Downstream cues are checked first only where they are unambiguous; "why"
    # always wins for direction because it is the canonical cause question.
    for cue in _UPSTREAM_CUES:
        if cue in q:
            return TraversalDirection.UPSTREAM
    for cue in _DOWNSTREAM_CUES:
        if cue in q:
            return TraversalDirection.DOWNSTREAM
    # Default: most causal-memory questions ("what was the basis for X") are
    # really asking for the causes behind a decision.
    return TraversalDirection.UPSTREAM


def _detect_edge_filter(question: str) -> EdgeKind | None:
    # Word-boundary match so a cue like "cause" does not fire inside "because".
    # We match a cue when it appears as a whole word or as the stem of a word
    # (e.g. "constraint"/"constraints", "produce"/"produced").
    words = re.findall(r"[a-z]+", question.lower())
    for cue, kind in _EDGE_CUES.items():
        if any(w == cue or w.startswith(cue) for w in words):
            return kind
    return None


def _keywords(question: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", question.lower())
    return [w for w in words if w not in _STOPWORDS]


def parse(question: str, *, max_depth: int = 4) -> GraphQuery:
    """Parse an English question into a structured :class:`GraphQuery`."""
    return GraphQuery(
        raw=question,
        direction=_detect_direction(question),
        keywords=_keywords(question),
        edge_filter=_detect_edge_filter(question),
        max_depth=max_depth,
    )


def _node_match_score(query: GraphQuery, node: CausalNode) -> float:
    """Score how well a node answers the question.

    Blends a keyword-overlap signal with a semantic (hashed-embedding) cosine
    so that lexical matches are rewarded while paraphrases still rank.
    """
    text = node.text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))

    # Keyword overlap component.
    keyword_score = 0.0
    if query.keywords:
        hits = sum(1 for kw in query.keywords if kw in tokens)
        keyword_score = hits / len(query.keywords)

    # Semantic component (deterministic hashed embedding fallback is fine).
    semantic_score = cosine(embed(query.raw), embed(node.text))

    # NB: the decision-node preference is applied as a tie-breaker in
    # _find_entry_node, NOT added here, so it can never inflate a node above
    # the min_match threshold on its own (which would cause false positives).
    return 0.6 * keyword_score + 0.4 * max(0.0, semantic_score)


def _find_entry_node(query: GraphQuery, graph: CausalGraph) -> tuple[CausalNode | None, float]:
    """Pick the graph node that best matches the question.

    Ties on the base score are broken in favour of ``decision`` nodes, which
    are the most natural entry point for a "why" question. The tie-breaker
    never changes the score itself, so it cannot push an otherwise-irrelevant
    node above the ``min_match`` threshold.
    """
    best: CausalNode | None = None
    best_score = 0.0
    best_is_decision = False
    for node in graph.nodes.values():
        score = _node_match_score(query, node)
        is_decision = node.kind == "decision"
        if score > best_score or (score == best_score and is_decision and not best_is_decision):
            best_score = score
            best = node
            best_is_decision = is_decision
    return best, best_score


def query(
    question: str,
    graph: CausalGraph,
    *,
    max_depth: int = 4,
    min_match: float = 0.05,
) -> GraphQueryResult:
    """Answer an English question by traversing the causal graph.

    1. Parse the question (direction + keywords + optional edge filter).
    2. Resolve the best-matching entry node in the graph.
    3. Walk the graph from that node — upstream for cause questions,
       downstream for effect questions — and return the causal path.

    Parameters
    ----------
    question:
        The plain-English question.
    graph:
        The :class:`CausalGraph` to traverse.
    max_depth:
        Maximum traversal depth handed to the graph's BFS.
    min_match:
        Minimum entry-node match score; below this the question is treated
        as unanswerable (no confident anchor node).
    """
    parsed = parse(question, max_depth=max_depth)
    entry, score = _find_entry_node(parsed, graph)

    if entry is None or score < min_match:
        return GraphQueryResult(
            question=question,
            direction=parsed.direction,
            entry_node=None,
            match_score=score,
            path=[],
        )

    # Compile the parsed query into an actual graph traversal.
    if parsed.direction is TraversalDirection.UPSTREAM:
        chain = graph.explain(entry.node_id, max_depth=max_depth)
    else:
        chain = graph.downstream(entry.node_id, max_depth=max_depth)

    # graph.explain() includes the entry node itself as the first element;
    # graph.downstream() does not. Normalise both to "path excluding entry".
    steps: list[TraversalStep] = []
    depth = 0
    for node in chain:
        if node.node_id == entry.node_id:
            continue
        depth += 1
        steps.append(TraversalStep(node=node, depth=depth))

    # Optional edge-kind filter: keep only nodes reachable by that edge kind
    # directly from the entry node. This is a light refinement on top of the
    # BFS chain so that "what constraints shaped X" returns constraint nodes.
    if parsed.edge_filter is not None:
        directly_related = _direct_neighbours(graph, entry.node_id, parsed)
        # Respect the explicit constraint: when the question names a specific
        # edge kind, return only nodes reachable by that edge. If nothing
        # matches, return an empty path rather than misleading unrelated
        # nodes the user did not ask for.
        steps = [s for s in steps if s.node.node_id in directly_related]

    return GraphQueryResult(
        question=question,
        direction=parsed.direction,
        entry_node=entry,
        match_score=score,
        path=steps,
    )


def _direct_neighbours(graph: CausalGraph, node_id: str, parsed: GraphQuery) -> set[str]:
    """Node ids one hop from ``node_id`` along the parsed edge filter.

    Uses the graph's public ``to_dict`` edge listing so we do not reach into
    its private adjacency maps.
    """
    if parsed.edge_filter is None:
        return set()
    payload = graph.to_dict()
    wanted = parsed.edge_filter.value
    neighbours: set[str] = set()
    for edge in payload["edges"]:
        if edge["kind"] != wanted:
            continue
        if parsed.direction is TraversalDirection.UPSTREAM and edge["dst"] == node_id:
            neighbours.add(edge["src"])
        elif parsed.direction is TraversalDirection.DOWNSTREAM and edge["src"] == node_id:
            neighbours.add(edge["dst"])
    return neighbours


__all__ = [
    "TraversalDirection",
    "GraphQuery",
    "TraversalStep",
    "GraphQueryResult",
    "parse",
    "query",
]
