"""Semantic loop and stuck-state detection (WP-02).

The circuit breaker counts *failures*. Agents also get stuck while every call *succeeds*:
``git status`` ten times, ``ls`` → ``cat`` → ``ls`` → ``cat``, or one failing command
re-issued with small edits. This detector keeps a sliding window of the last ``window``
calls per session (default 10) with each call's outcome and output hash, and reports:

``DUPLICATE_CALL_LOOP``
    The same tool with identical (canonical) arguments ``duplicate_threshold`` times in
    the window, while the earlier identical calls made no progress (same output, or
    failed). Identical calls whose outputs change (polling a job, tailing a log) are
    progress and are not flagged.
``OSCILLATION_LOOP``
    A cycle of 2 or 3 distinct calls repeated ``oscillation_cycles`` times, with no new
    output across the cycles.
``SEMANTIC_THRASHING_LOOP``
    Near-identical (not identical) calls to the same tool, similarity above
    ``similarity_threshold`` by normalised Levenshtein or token-set Jaccard, where at least
    ``thrash_failures`` of the similar earlier calls failed: the agent keeps tweaking a
    failing command.
``NO_PROGRESS``
    The last ``no_progress_window`` calls were all non-mutating and produced no output
    the window had not already seen.
``REPEATED_FAILURE``
    The same tool failed ``failure_threshold`` times in a row (any arguments).

Each finding carries the evidence (the repeated calls, similarities, outputs) so the
dashboard can show *why*. Detection is per session, so concurrent sessions never mix.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agentwatch.protect.model import Finding, Severity, Stage, Verdict
from agentwatch.security.owasp import OwaspVector

MUTATING_TOOL = re.compile(
    r"(?i)(write|edit|create|update|delete|remove|move|rename|put|patch|post|send|insert|commit|push|apply|install|deploy|set_|save)"
)
DEFAULT_REPEATABLE = re.compile(
    r"(?i)(wait|sleep|poll|status|health|heartbeat|ping|tail|watch|progress|check_job|get_job)"
)


def canonical_args(arguments: Any) -> str:
    try:
        return json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(arguments)


def levenshtein_similarity(a: str, b: str, limit: int = 600) -> float:
    """``1 - lev(a, b) / max(|a|, |b|)``, computed on the first ``limit`` characters."""
    a, b = a[:limit], b[:limit]
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def token_jaccard(a: str, b: str) -> float:
    ta, tb = set(_TOKEN.findall(a.lower())), set(_TOKEN.findall(b.lower()))
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def _semantic_text(arguments: Any) -> str:
    """The text that carries a call's meaning: its command/query if it has one."""
    if isinstance(arguments, dict):
        for k in (
            "command",
            "cmd",
            "query",
            "sql",
            "code",
            "script",
            "url",
            "path",
            "pattern",
            "q",
            "search",
        ):
            v = arguments.get(k)
            if isinstance(v, str) and v.strip():
                return v
    return canonical_args(arguments)


@dataclass
class CallRecord:
    call_id: str
    tool: str
    args_hash: str
    text: str
    mutating: bool
    at: float = field(default_factory=time.time)
    outcome: str | None = None  # "success" | "failure" | None (not yet known)
    output_hash: str | None = None

    @property
    def signature(self) -> str:
        return f"{self.tool}:{self.args_hash}"


@dataclass
class LoopConfig:
    window: int = 10
    duplicate_threshold: int = 3
    oscillation_cycles: int = 2
    similarity_threshold: float = 0.80
    thrash_failures: int = 3
    no_progress_window: int = 8
    failure_threshold: int = 4
    repeatable_tools: re.Pattern[str] = DEFAULT_REPEATABLE
    repeatable_multiplier: int = 3  # repeatable tools tolerate this many times the duplicates
    verdict: Verdict = Verdict.ESCALATE_HITL  # what a confirmed loop asks for


class LoopDetector:
    """Per-session sliding-window loop detector. Thread-safe."""

    def __init__(self, config: LoopConfig | None = None) -> None:
        self.config = config or LoopConfig()
        self._sessions: dict[str, deque[CallRecord]] = {}
        self._by_call: dict[str, CallRecord] = {}
        self._lock = threading.Lock()

    def window(self, session_id: str) -> list[CallRecord]:
        with self._lock:
            return list(self._sessions.get(session_id, ()))

    def reset(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
                self._by_call.clear()
            else:
                for r in self._sessions.pop(session_id, ()):
                    self._by_call.pop(r.call_id, None)

    # ── pre-execution: would this call continue a loop? ────────────────────────────
    def check(self, session_id: str, call_id: str, tool: str, arguments: Any) -> list[Finding]:
        canon = canonical_args(arguments)
        rec = CallRecord(
            call_id=call_id,
            tool=tool,
            args_hash=hashlib.sha256(f"{tool}\x00{canon}".encode()).hexdigest()[:20],
            text=_semantic_text(arguments),
            mutating=bool(MUTATING_TOOL.search(tool)),
        )
        with self._lock:
            hist = self._sessions.setdefault(session_id, deque(maxlen=self.config.window))
            prior = list(hist)
            findings = self._detect(prior, rec)
            hist.append(rec)
            self._by_call[call_id] = rec
            if len(self._by_call) > 50_000:  # bound memory across many sessions
                live = {r.call_id for d in self._sessions.values() for r in d}
                self._by_call = {k: v for k, v in self._by_call.items() if k in live}
        return findings

    # ── post-execution: what happened ──────────────────────────────────────────────
    def record_result(self, call_id: str, *, success: bool, output: Any = None) -> None:
        with self._lock:
            rec = self._by_call.get(call_id)
            if rec is None:
                return
            rec.outcome = "success" if success else "failure"
            rec.output_hash = hashlib.sha256(canonical_args(output).encode()).hexdigest()[:20]

    # ── detection ──────────────────────────────────────────────────────────────────
    def _detect(self, prior: list[CallRecord], cur: CallRecord) -> list[Finding]:
        cfg = self.config
        out: list[Finding] = []
        repeatable = bool(cfg.repeatable_tools.search(cur.tool))
        seq = [*prior, cur]

        # 1. exact duplicates without progress
        same = [r for r in prior if r.signature == cur.signature]
        threshold = cfg.duplicate_threshold * (cfg.repeatable_multiplier if repeatable else 1)
        if len(same) + 1 >= threshold:
            outputs = {r.output_hash for r in same if r.outcome == "success"}
            failures = sum(r.outcome == "failure" for r in same)
            unknown = sum(r.outcome is None for r in same)
            progressing = len(outputs) >= max(2, len(same) - failures - unknown)
            if not progressing or failures >= len(same) - unknown:
                out.append(
                    Finding(
                        Stage.LOOP,
                        "DUPLICATE_CALL_LOOP",
                        Severity.HIGH,
                        f"'{cur.tool}' called {len(same) + 1} times with identical arguments in the last {len(seq)} calls, without new results",
                        {
                            "tool": cur.tool,
                            "occurrences": len(same) + 1,
                            "window": len(seq),
                            "distinct_outputs": len(outputs),
                            "failures": failures,
                            "calls": [r.call_id for r in same],
                        },
                        OwaspVector.TOOL_ABUSE.value,
                        cfg.verdict,
                    )
                )

        # 2. oscillation: cycle of 2-3 distinct signatures repeated
        for period in (2, 3):
            span = period * cfg.oscillation_cycles
            if len(seq) < span:
                continue
            tail = seq[-span:]
            pattern = [r.signature for r in tail[:period]]
            if len(set(pattern)) != period:
                continue
            if all(tail[i].signature == pattern[i % period] for i in range(span)):
                # progress check: every position's outputs identical across cycles
                stale = True
                for pos in range(period):
                    outs = {
                        tail[i].output_hash
                        for i in range(pos, span, period)
                        if tail[i].outcome == "success" and tail[i] is not cur
                    }
                    if len(outs) > 1:
                        stale = False
                if stale and not any(r.mutating for r in tail):
                    cycle = " -> ".join(r.tool for r in tail[:period])
                    out.append(
                        Finding(
                            Stage.LOOP,
                            "OSCILLATION_LOOP",
                            Severity.HIGH,
                            f"Oscillating between {period} calls ({cycle}) for {cfg.oscillation_cycles} cycles without new results",
                            {
                                "cycle": [r.tool for r in tail[:period]],
                                "period": period,
                                "cycles": cfg.oscillation_cycles,
                                "calls": [r.call_id for r in tail],
                            },
                            OwaspVector.TOOL_ABUSE.value,
                            cfg.verdict,
                        )
                    )
                    break

        # 3. semantic thrashing: near-identical retries of a failing call
        similar: list[tuple[CallRecord, float, float]] = []
        for r in prior:
            if r.tool != cur.tool or r.signature == cur.signature:
                continue
            lev = levenshtein_similarity(r.text, cur.text)
            jac = token_jaccard(r.text, cur.text)
            if max(lev, jac) >= cfg.similarity_threshold:
                similar.append((r, round(lev, 3), round(jac, 3)))
        failed_similar = [s for s in similar if s[0].outcome == "failure"]
        if len(failed_similar) >= cfg.thrash_failures:
            out.append(
                Finding(
                    Stage.LOOP,
                    "SEMANTIC_THRASHING_LOOP",
                    Severity.HIGH,
                    f"'{cur.tool}' retried with small variations after {len(failed_similar)} similar failures",
                    {
                        "tool": cur.tool,
                        "similar_failures": [
                            {
                                "call_id": r.call_id,
                                "levenshtein": lv,
                                "jaccard": jc,
                                "text": r.text[:160],
                            }
                            for r, lv, jc in failed_similar
                        ],
                        "threshold": cfg.similarity_threshold,
                    },
                    OwaspVector.TOOL_ABUSE.value,
                    cfg.verdict,
                )
            )

        # 4. repeated failure of the same tool (any arguments)
        streak = 0
        for r in reversed(prior):
            if r.tool != cur.tool:
                continue
            if r.outcome == "failure":
                streak += 1
            else:
                break
        if streak >= cfg.failure_threshold:
            out.append(
                Finding(
                    Stage.LOOP,
                    "REPEATED_FAILURE",
                    Severity.HIGH,
                    f"'{cur.tool}' failed {streak} times in a row",
                    {"tool": cur.tool, "consecutive_failures": streak},
                    OwaspVector.TOOL_ABUSE.value,
                    cfg.verdict,
                )
            )

        # 5. no progress: only reads, nothing new seen
        n = cfg.no_progress_window
        if len(prior) >= n and not out:
            recent = prior[-n:]
            if all(r.outcome is not None for r in recent) and not any(r.mutating for r in recent):
                seen: set[str | None] = set()
                new = 0
                for r in prior[: len(prior) - n]:
                    seen.add(r.output_hash)
                for r in recent:
                    if r.output_hash not in seen:
                        new += 1
                        seen.add(r.output_hash)
                if new <= 1 and len({r.signature for r in recent}) <= n // 2:
                    out.append(
                        Finding(
                            Stage.LOOP,
                            "NO_PROGRESS",
                            Severity.MEDIUM,
                            f"The last {n} calls only read and produced {new} new result(s)",
                            {
                                "window": n,
                                "new_results": new,
                                "tools": sorted({r.tool for r in recent}),
                            },
                            OwaspVector.TOOL_ABUSE.value,
                            Verdict.ALLOW,
                        )
                    )
        return out
