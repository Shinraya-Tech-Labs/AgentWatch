"""
MEM-007 — Natural Language Memory Query.

"What did we decide about the database last week?"
Returns structured memory results with citations.

This is a heuristic NL → structured-filter parser that operates on the
existing memory store. When an embedding model is available it also
performs a semantic re-rank.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from panoptis.scoring.drift import cosine, embed


@dataclass
class QueryFilter:
    keywords: list[str] = field(default_factory=list)
    since: datetime | None = None
    topic: str | None = None
    raw: str = ""


@dataclass
class QueryResult:
    key: str
    value: Any
    score: float
    timestamp: datetime | None
    citation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "score": self.score,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "citation": self.citation,
        }


_TIME_PATTERNS = [
    (re.compile(r"last\s+week", re.I), timedelta(days=7)),
    (re.compile(r"last\s+sprint", re.I), timedelta(days=14)),
    (re.compile(r"last\s+month", re.I), timedelta(days=30)),
    (re.compile(r"yesterday", re.I), timedelta(days=1)),
    (re.compile(r"today", re.I), timedelta(hours=24)),
    (re.compile(r"this\s+year", re.I), timedelta(days=365)),
]

_TOPIC_ALIASES: dict[str, set[str]] = {
    "database": {"database", "databases", "db", "postgres", "postgresql", "mysql", "sqlite", "sql"},
}


def _expand_terms(*terms: str) -> set[str]:
    expanded: set[str] = set()
    for term in terms:
        normalized = term.lower().strip()
        if not normalized:
            continue
        expanded.add(normalized)
        for canonical, aliases in _TOPIC_ALIASES.items():
            if normalized == canonical or normalized in aliases:
                expanded.update(aliases)
                expanded.add(canonical)
    return expanded


def parse(question: str, *, now: datetime | None = None) -> QueryFilter:
    now = now or datetime.now(UTC)
    f = QueryFilter(raw=question)

    for pat, delta in _TIME_PATTERNS:
        if pat.search(question):
            f.since = now - delta
            break

    m = re.search(r"about\s+(?:the\s+)?(\w[\w\s]{1,30})", question, re.I)
    if m:
        f.topic = m.group(1).strip().rstrip(".?!")

    words = re.findall(r"[A-Za-z]{4,}", question.lower())
    stop = {
        "what",
        "when",
        "where",
        "which",
        "about",
        "last",
        "this",
        "year",
        "week",
        "month",
        "today",
        "did",
        "have",
        "the",
        "for",
    }
    f.keywords = [w for w in words if w not in stop]
    return f


def query(
    question: str,
    memories: Iterable[dict[str, Any]],
    *,
    limit: int = 10,
    semantic: bool = True,
) -> list[QueryResult]:
    f = parse(question)
    candidates: list[tuple[float, dict[str, Any]]] = []
    keyword_terms = {keyword: _expand_terms(keyword) for keyword in f.keywords}
    topic_terms = _expand_terms(f.topic or "")

    q_vec = embed(question) if semantic else None
    for m in memories:
        text = " ".join(str(v) for v in m.values() if isinstance(v, (str, int, float)))
        tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        score = 0.0

        # keyword overlap
        if keyword_terms:
            matched_keywords = sum(
                1 for term_group in keyword_terms.values() if term_group.intersection(tokens)
            )
            score += matched_keywords / len(keyword_terms)

        # topic match
        if topic_terms and topic_terms.intersection(tokens):
            score += 0.5

        # time filter
        ts = m.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                ts = None
        if f.since and isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if ts >= f.since:
                score += 0.3
            else:
                score -= 0.5

        # semantic rerank
        if q_vec is not None:
            score += 0.4 * cosine(q_vec, embed(text))

        candidates.append((score, m))

    candidates.sort(key=lambda x: x[0], reverse=True)
    results: list[QueryResult] = []
    for s, m in candidates[:limit]:
        ts = m.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                ts = None
        results.append(
            QueryResult(
                key=str(m.get("key", "?")),
                value=m.get("value"),
                score=s,
                timestamp=ts if isinstance(ts, datetime) else None,
                citation=f"memory[{m.get('key', '?')}]",
            )
        )
    return results


__all__ = ["QueryFilter", "QueryResult", "parse", "query"]
