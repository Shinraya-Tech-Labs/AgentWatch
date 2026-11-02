"""Semantic loop and stuck-state detection (WP-02, TO-020).

The circuit breaker counts *failures*. Agents also get stuck while every call *succeeds*:
``git status`` ten times, ``ls`` → ``cat`` → ``ls`` → ``cat``, or one failing command
re-issued with small edits. This detector keeps a sliding window of the last ``window``
calls per session (default 10) with each call's outcome and output hash, and reports:

``DUPLICATE_CALL_LOOP``
    The same tool with identical (canonical) arguments ``duplicate_threshold`` times in
    the window (default 3), while the most recent identical calls made no progress: they
    failed, or returned the same output. Identical calls whose outputs change (polling a
    job, tailing a log) are progress and are not flagged. A *new* mutating call in between
    (a different edit, then the same test run) also counts as progress: only the identical
    calls after it are counted.
``OSCILLATION_LOOP``
    A cycle of 2 or 3 distinct read-only calls repeated ``oscillation_cycles`` times
    (A→B→A→B, A→B→C→A→B→C), with no new output across the cycles.
``SEMANTIC_THRASHING_LOOP``
    Near-identical (not identical) calls to the same tool, similarity strictly above
    ``similarity_threshold`` (default 0.80) by normalised Levenshtein or token-set Jaccard,
    where at least ``thrash_failures`` of the similar earlier calls failed *with a
    repeating error*: the agent keeps tweaking a failing command and the failure does not
    change.
``NO_PROGRESS``
    The last ``no_progress_window`` calls were all non-mutating and produced no output
    the window had not already seen (informational; recommends ALLOW).
``REPEATED_FAILURE``
    The same tool failed ``failure_threshold`` times in a row (any arguments).

Each finding carries the evidence (the repeated calls, similarities, outputs) so the
dashboard can show *why*. Detection is per session (the pipeline passes the tenant-scoped
session key), so concurrent sessions never mix. Memory is bounded: ``window`` records per
session and at most ``max_sessions`` sessions (least recently used are dropped).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

from panoptis.protect.model import Finding, Severity, Stage, Verdict
from panoptis.security.owasp import OwaspVector

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


def _bounded_distance(a: str, b: str, k: int) -> int:
    """Levenshtein distance if it is at most ``k``, else ``k + 1`` (Ukkonen band, O(n·k))."""
    la, lb = len(a), len(b)
    big = k + 1
    if abs(la - lb) > k:
        return big
    prev = [j if j <= k else big for j in range(lb + 1)]
    for i in range(1, la + 1):
        lo, hi = max(1, i - k), min(lb, i + k)
        cur = [big] * (lb + 1)
        if i <= k:
            cur[0] = i
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(lo, hi + 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != b[j - 1]))
            if v > big:
                v = big
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > k:
            return big
        prev = cur
    return min(prev[lb], big)


def levenshtein_similarity(
    a: str, b: str, limit: int = 600, at_least: float | None = None
) -> float:
    """``1 - lev(a, b) / max(|a|, |b|)``, computed on the first ``limit`` characters.

    With ``at_least``, only similarities ``>= at_least`` are computed exactly (a banded
    computation, much cheaper); anything lower is returned as ``0.0``.
    """
    a, b = a[:limit], b[:limit]
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    n = max(len(a), len(b))
    k = n if at_least is None else int((1.0 - at_least) * n)
    d = _bounded_distance(a, b, k)
    if d > k:
        return 0.0
    return 1.0 - d / n


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
    max_sessions: int = 10_000  # sessions tracked at once; the least recently used are dropped
    similarity_chars: int = 256  # characters compared by Levenshtein (Jaccard uses all)

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError("LoopConfig.window must be at least 2")
        for name in ("duplicate_threshold", "oscillation_cycles", "thrash_failures"):
            if getattr(self, name) < 2:
                raise ValueError(f"LoopConfig.{name} must be at least 2")
        if self.failure_threshold < 1 or self.no_progress_window < 2:
            raise ValueError("LoopConfig.failure_threshold/no_progress_window out of range")
        if not 0.0 < self.similarity_threshold < 1.0:
            raise ValueError("LoopConfig.similarity_threshold must be between 0 and 1")
        if self.max_sessions < 1 or self.repeatable_multiplier < 1:
            raise ValueError("LoopConfig.max_sessions/repeatable_multiplier must be positive")
        if not isinstance(self.verdict, Verdict):
            self.verdict = Verdict(self.verdict)


class LoopDetector:
    """Per-session sliding-window loop detector. Thread-safe and memory-bounded."""

    def __init__(self, config: LoopConfig | None = None) -> None:
        self.config = config or LoopConfig()
        self._sessions: OrderedDict[str, deque[CallRecord]] = OrderedDict()
        self._by_call: dict[tuple[str, str], CallRecord] = {}
        self._lock = threading.Lock()

    def window(self, session_id: str) -> list[CallRecord]:
        with self._lock:
            return list(self._sessions.get(session_id, ()))

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "records": sum(len(d) for d in self._sessions.values()),
                "indexed_calls": len(self._by_call),
            }

    def reset(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
                self._by_call.clear()
            else:
                self._drop(session_id)

    def _drop(self, session_id: str) -> None:
        for r in self._sessions.pop(session_id, ()):
            self._by_call.pop((session_id, r.call_id), None)

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
            hist = self._sessions.get(session_id)
            if hist is None:
                hist = deque(maxlen=self.config.window)
                self._sessions[session_id] = hist
                while len(self._sessions) > self.config.max_sessions:
                    self._drop(next(iter(self._sessions)))
            else:
                self._sessions.move_to_end(session_id)
            prior = list(hist)
            findings = self._detect(prior, rec)
            if len(hist) == hist.maxlen:
                self._by_call.pop((session_id, hist[0].call_id), None)
            hist.append(rec)
            self._by_call[(session_id, call_id)] = rec
        return findings

    # ── post-execution: what happened ──────────────────────────────────────────────
    def record_result(
        self,
        call_id: str,
        *,
        success: bool,
        output: Any = None,
        session_id: str | None = None,
    ) -> None:
        """Attach a call's outcome. Pass ``session_id``: call ids are only unique per session."""
        out_hash = hashlib.sha256(canonical_args(output).encode()).hexdigest()[:20]
        with self._lock:
            if session_id is not None:
                rec = self._by_call.get((session_id, call_id))
            else:
                matches = [r for (s, c), r in self._by_call.items() if c == call_id]
                if len(matches) > 1:
                    raise ValueError(f"call id {call_id!r} is in several sessions; pass session_id")
                rec = matches[0] if matches else None
            if rec is None:
                return
            rec.outcome = "success" if success else "failure"
            rec.output_hash = out_hash

    # ── detection ──────────────────────────────────────────────────────────────────
    def _finding(self, code: str, message: str, evidence: dict[str, Any]) -> Finding:
        return Finding(
            Stage.LOOP,
            code,
            Severity.HIGH,
            message,
            evidence,
            OwaspVector.TOOL_ABUSE.value,
            self.config.verdict,
        )

    def _detect(self, prior: list[CallRecord], cur: CallRecord) -> list[Finding]:
        cfg = self.config
        out: list[Finding] = []
        seq = [*prior, cur]
        if (f := self._duplicates(prior, cur)) is not None:
            out.append(f)
        if (f := self._oscillation(seq, cur)) is not None:
            out.append(f)
        if (f := self._thrashing(prior, cur)) is not None:
            out.append(f)

        # repeated failure of the same tool (any arguments)
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
                self._finding(
                    "REPEATED_FAILURE",
                    f"'{cur.tool}' failed {streak} times in a row",
                    {"tool": cur.tool, "consecutive_failures": streak},
                )
            )

        if not out and (f := self._no_progress(prior)) is not None:
            out.append(f)
        return out

    def _duplicates(self, prior: list[CallRecord], cur: CallRecord) -> Finding | None:
        cfg = self.config
        repeatable = bool(cfg.repeatable_tools.search(cur.tool))
        threshold = cfg.duplicate_threshold * (cfg.repeatable_multiplier if repeatable else 1)
        # A new mutating call (one not seen before in the window) changes the state the
        # identical calls observe: only the identical calls after it are counted.
        start = 0
        seen: set[str] = set()
        for i, r in enumerate(prior):
            if (
                r.mutating
                and r.outcome != "failure"
                and r.signature not in seen
                and r.signature != cur.signature
            ):
                start = i + 1
            seen.add(r.signature)
        same = [r for r in prior[start:] if r.signature == cur.signature]
        if len(same) + 1 < threshold:
            return None
        # Progress evidence: the most recent identical calls returned different outputs.
        recent = same[-(threshold - 1) :]
        outputs = {r.output_hash for r in recent if r.outcome == "success"}
        failures = sum(r.outcome == "failure" for r in recent)
        if len(outputs) > 1:
            return None
        return self._finding(
            "DUPLICATE_CALL_LOOP",
            f"'{cur.tool}' called {len(same) + 1} times with identical arguments in the last "
            f"{len(prior) + 1} calls, without new results",
            {
                "tool": cur.tool,
                "occurrences": len(same) + 1,
                "threshold": threshold,
                "window": len(prior) + 1,
                "distinct_recent_outputs": len(outputs),
                "recent_failures": failures,
                "calls": [r.call_id for r in same],
            },
        )

    def _oscillation(self, seq: list[CallRecord], cur: CallRecord) -> Finding | None:
        cfg = self.config
        for period in (2, 3):
            span = period * cfg.oscillation_cycles
            if len(seq) < span:
                continue
            tail = seq[-span:]
            pattern = [r.signature for r in tail[:period]]
            if len(set(pattern)) != period:
                continue
            if not all(tail[i].signature == pattern[i % period] for i in range(span)):
                continue
            if any(r.mutating for r in tail):
                continue
            # progress check: each position's outputs must be identical across cycles
            stale = True
            for pos in range(period):
                outs = {
                    tail[i].output_hash
                    for i in range(pos, span, period)
                    if tail[i].outcome == "success" and tail[i] is not cur
                }
                if len(outs) > 1:
                    stale = False
            if stale:
                cycle = " -> ".join(r.tool for r in tail[:period])
                return self._finding(
                    "OSCILLATION_LOOP",
                    f"Oscillating between {period} calls ({cycle}) for "
                    f"{cfg.oscillation_cycles} cycles without new results",
                    {
                        "cycle": [r.tool for r in tail[:period]],
                        "period": period,
                        "cycles": cfg.oscillation_cycles,
                        "calls": [r.call_id for r in tail],
                    },
                )
        return None

    def _thrashing(self, prior: list[CallRecord], cur: CallRecord) -> Finding | None:
        cfg = self.config
        t = cfg.similarity_threshold
        failed_similar: list[tuple[CallRecord, float | None, float]] = []
        for r in prior:
            if r.tool != cur.tool or r.signature == cur.signature or r.outcome != "failure":
                continue
            jac = token_jaccard(r.text, cur.text)
            lev = (
                levenshtein_similarity(r.text, cur.text, cfg.similarity_chars, at_least=t)
                if jac <= t
                else None  # not needed: the token overlap already exceeds the threshold
            )
            if max(lev or 0.0, jac) > t:
                failed_similar.append((r, None if lev is None else round(lev, 3), round(jac, 3)))
        if len(failed_similar) < cfg.thrash_failures:
            return None
        # Supporting evidence: the edits did not change the failure (an error repeats).
        errors = [r.output_hash for r, _, _ in failed_similar]
        if len(set(errors)) == len(errors):
            return None
        return self._finding(
            "SEMANTIC_THRASHING_LOOP",
            f"'{cur.tool}' retried with small variations after {len(failed_similar)} similar "
            "failures",
            {
                "tool": cur.tool,
                "similar_failures": [
                    {"call_id": r.call_id, "levenshtein": lv, "jaccard": jc, "text": r.text[:160]}
                    for r, lv, jc in failed_similar
                ],
                "distinct_errors": len(set(errors)),
                "threshold": t,
            },
        )

    def _no_progress(self, prior: list[CallRecord]) -> Finding | None:
        n = self.config.no_progress_window
        if len(prior) < n:
            return None
        recent = prior[-n:]
        if any(r.outcome is None for r in recent) or any(r.mutating for r in recent):
            return None
        seen: set[str | None] = {r.output_hash for r in prior[: len(prior) - n]}
        new = 0
        for r in recent:
            if r.output_hash not in seen:
                new += 1
                seen.add(r.output_hash)
        if new > 1 or len({r.signature for r in recent}) > n // 2:
            return None
        return Finding(
            Stage.LOOP,
            "NO_PROGRESS",
            Severity.MEDIUM,
            f"The last {n} calls only read and produced {new} new result(s)",
            {"window": n, "new_results": new, "tools": sorted({r.tool for r in recent})},
            OwaspVector.TOOL_ABUSE.value,
            Verdict.ALLOW,
        )
