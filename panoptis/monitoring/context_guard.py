"""Context-window boundary monitor: system prompt eviction and remaining capacity.

A request can stay under the model's token limit and still have lost the agent's original
instructions, because a trimming layer dropped or cut the system prompt to make room. This
guard looks at both things for each outgoing LLM request:

* **Instruction integrity.** The first request of a run (or an explicit
  :meth:`ContextGuard.register_anchor`) fixes an *anchor*: one SHA-256 fingerprint per system
  instruction segment. Later requests of the same run must still carry every anchored segment.
  If one is gone the guard reports ``SYSTEM_PROMPT_EVICTED`` with a reason of ``missing`` (no
  system instructions at all), ``truncated`` (less instruction text than the anchor) or
  ``altered`` (same or more text, but an anchored segment is not present as written). Extra
  segments added later are fine.
* **Capacity.** ``headroom = context_limit - (prompt_tokens + reserved_completion)`` in integer
  tokens. When it is below ``min_headroom_percent`` of the limit (default 10), or negative, the
  guard reports ``CONTEXT_WINDOW_EXHAUSTED``. Exactly at the threshold is not reported.

Token counts are never estimated. ``prompt_tokens`` must be a count the provider reported (a
``usage`` block, or a count-tokens call) and the context limit must be configured; when either
is missing the result says so (``CONTEXT_USAGE_UNAVAILABLE`` / ``CONTEXT_LIMIT_UNKNOWN``) and
no capacity verdict is made.

Findings never contain prompt text, only counts and the first 12 hex digits of a fingerprint.
Anchors hold digests and character counts only, and are kept per run key with a bounded
number of runs, so one run's prompt never vouches for another's.

Standard library only; request shapes follow the OpenAI (``messages`` / ``input`` /
``instructions``) and Anthropic (``system`` / ``messages``) payloads that the LLM API
normalizers in ``panoptis.events.normalizers.llm_api`` read.
"""

from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field
from typing import Any

from panoptis.events.model import Diagnostic

SYSTEM_PROMPT_EVICTED = "SYSTEM_PROMPT_EVICTED"
CONTEXT_WINDOW_EXHAUSTED = "CONTEXT_WINDOW_EXHAUSTED"
CONTEXT_USAGE_UNAVAILABLE = "CONTEXT_USAGE_UNAVAILABLE"
CONTEXT_LIMIT_UNKNOWN = "CONTEXT_LIMIT_UNKNOWN"

DEFAULT_MIN_HEADROOM_PERCENT = 10
DEFAULT_MAX_RUNS = 1024

_SYSTEM_ROLES = frozenset({"system", "developer"})
_PROMPT_TOKEN_KEYS = ("prompt_tokens", "input_tokens")
# Anthropic reports cached prompt tokens separately from ``input_tokens``; they still occupy
# the context window.
_CACHED_PROMPT_TOKEN_KEYS = ("cache_creation_input_tokens", "cache_read_input_tokens")
_RESERVED_KEYS = ("max_completion_tokens", "max_output_tokens", "max_tokens")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text_of(content: Any) -> list[str]:
    """Text pieces of a message ``content``: a string, or a list of ``{"text": ...}`` parts."""
    if isinstance(content, str):
        return [content]
    pieces: list[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                pieces.append(part)
            elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
                pieces.append(part["text"])
    return pieces


def extract_system_segments(request: Mapping[str, Any]) -> list[str]:
    """System instruction segments of an OpenAI- or Anthropic-style request, in order.

    Sources: top-level ``system`` (string or list of text blocks), ``instructions``, and
    ``messages`` / ``input`` entries whose role is ``system`` or ``developer``. Blank segments
    are dropped.
    """
    segments: list[str] = []
    system = request.get("system")
    if isinstance(system, str) or isinstance(system, list):
        segments.extend(_text_of(system))
    if isinstance(request.get("instructions"), str):
        segments.append(request["instructions"])
    for key in ("messages", "input"):
        items = request.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping) and item.get("role") in _SYSTEM_ROLES:
                segments.extend(_text_of(item.get("content")))
    return [s for s in segments if s.strip()]


def _count(value: Any) -> int | None:
    """A reported token count: a non-negative int (bool is not a count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def reported_prompt_tokens(usage: Mapping[str, Any] | None) -> int | None:
    """Prompt tokens from a provider ``usage`` block, or None if it does not report them."""
    if not isinstance(usage, Mapping):
        return None
    base: int | None = None
    for key in _PROMPT_TOKEN_KEYS:
        base = _count(usage.get(key))
        if base is not None:
            break
    if base is None:
        return None
    return base + sum(_count(usage.get(k)) or 0 for k in _CACHED_PROMPT_TOKEN_KEYS)


def reserved_completion_tokens(request: Mapping[str, Any]) -> int | None:
    """Completion tokens the request reserves (``max_tokens`` and its variants), if any."""
    for key in _RESERVED_KEYS:
        value = _count(request.get(key))
        if value is not None:
            return value
    return None


@dataclass(frozen=True, slots=True)
class PromptAnchor:
    """Fingerprint of the original system instructions of one run. No prompt text."""

    segment_digests: tuple[str, ...]
    char_count: int

    @property
    def fingerprint(self) -> str:
        return _digest("\n".join(self.segment_digests))

    @property
    def short(self) -> str:
        return self.fingerprint[:12]


@dataclass(frozen=True, slots=True)
class GuardResult:
    """Outcome of one :meth:`ContextGuard.check`.

    ``prompt_intact`` is None when there was no anchor to compare against. ``headroom_tokens``
    and ``headroom_percent`` are None when a capacity verdict could not be made.
    """

    run_key: Hashable
    findings: tuple[Diagnostic, ...] = ()
    prompt_intact: bool | None = None
    eviction_reason: str | None = None
    anchor_registered: bool = False
    headroom_tokens: int | None = None
    headroom_percent: float | None = None
    context_limit: int | None = None
    prompt_tokens: int | None = None
    reserved_completion: int | None = None
    reserved_completion_reported: bool = False
    codes: frozenset[str] = field(default_factory=frozenset)

    @property
    def ok(self) -> bool:
        """True when neither ``SYSTEM_PROMPT_EVICTED`` nor ``CONTEXT_WINDOW_EXHAUSTED`` fired."""
        return not (self.codes & {SYSTEM_PROMPT_EVICTED, CONTEXT_WINDOW_EXHAUSTED})


class ContextGuard:
    """Per-run system prompt anchor and context headroom check. Not thread-safe."""

    def __init__(
        self,
        *,
        context_limits: Mapping[str, int] | None = None,
        default_context_limit: int | None = None,
        min_headroom_percent: int = DEFAULT_MIN_HEADROOM_PERCENT,
        max_runs: int = DEFAULT_MAX_RUNS,
    ) -> None:
        if isinstance(min_headroom_percent, bool) or not (0 <= min_headroom_percent <= 100):
            raise ValueError("min_headroom_percent must be an integer from 0 to 100")
        if isinstance(max_runs, bool) or max_runs < 1:
            raise ValueError("max_runs must be at least 1")
        limits = dict(context_limits or {})
        for model, limit in {**limits, "<default>": default_context_limit}.items():
            if limit is not None and (_count(limit) is None or limit == 0):
                raise ValueError(f"context limit for {model} must be a positive integer")
        self._limits = limits
        self._default_limit = default_context_limit
        self._min_percent = min_headroom_percent
        self._max_runs = max_runs
        self._anchors: OrderedDict[Hashable, PromptAnchor] = OrderedDict()

    # -- anchors ---------------------------------------------------------------------------

    def register_anchor(self, run_key: Hashable, request: Mapping[str, Any] | str) -> PromptAnchor:
        """Fix the original instructions of ``run_key`` (replacing any earlier anchor).

        ``request`` is a request payload, or the system prompt itself as a string. Raises
        ``ValueError`` when it carries no system instructions.
        """
        segments = [request] if isinstance(request, str) else extract_system_segments(request)
        segments = [s for s in segments if s.strip()]
        if not segments:
            raise ValueError("no system instructions to anchor")
        anchor = PromptAnchor(tuple(_digest(s) for s in segments), sum(len(s) for s in segments))
        self._anchors[run_key] = anchor
        self._anchors.move_to_end(run_key)
        while len(self._anchors) > self._max_runs:
            self._anchors.popitem(last=False)
        return anchor

    def anchor_for(self, run_key: Hashable) -> PromptAnchor | None:
        return self._anchors.get(run_key)

    def forget(self, run_key: Hashable) -> None:
        """Drop the anchor of a finished run."""
        self._anchors.pop(run_key, None)

    # -- checks ----------------------------------------------------------------------------

    def check(
        self,
        run_key: Hashable,
        request: Mapping[str, Any],
        *,
        model: str | None = None,
        usage: Mapping[str, Any] | None = None,
        prompt_tokens: int | None = None,
        reserved_completion: int | None = None,
        context_limit: int | None = None,
    ) -> GuardResult:
        """Check one outgoing request.

        The first request of a run that carries system instructions becomes the anchor and is
        not itself judged. ``prompt_tokens`` (or a ``usage`` block) is the count the provider
        reported for this prompt; ``reserved_completion`` defaults to the request's
        ``max_tokens`` family; ``context_limit`` defaults to the configured limit for ``model``.
        """
        findings: list[Diagnostic] = []
        intact: bool | None = None
        reason: str | None = None
        registered = False

        segments = extract_system_segments(request)
        anchor = self._anchors.get(run_key)
        if anchor is None:
            if segments:
                self.register_anchor(run_key, request)
                registered = True
                intact = True
        else:
            self._anchors.move_to_end(run_key)
            reason = self._eviction_reason(anchor, segments)
            intact = reason is None
            if reason is not None:
                findings.append(self._eviction_finding(anchor, segments, reason))

        capacity = self._capacity(
            request, model, usage, prompt_tokens, reserved_completion, context_limit, findings
        )
        codes = frozenset(f.code for f in findings)
        return GuardResult(
            run_key=run_key,
            findings=tuple(findings),
            prompt_intact=intact,
            eviction_reason=reason,
            anchor_registered=registered,
            codes=codes,
            **capacity,
        )

    @staticmethod
    def _eviction_reason(anchor: PromptAnchor, segments: list[str]) -> str | None:
        if not segments:
            return "missing"
        have = Counter(_digest(s) for s in segments)
        need = Counter(anchor.segment_digests)
        if not (need - have):
            return None
        return "truncated" if sum(len(s) for s in segments) < anchor.char_count else "altered"

    @staticmethod
    def _eviction_finding(anchor: PromptAnchor, segments: list[str], reason: str) -> Diagnostic:
        have = Counter(_digest(s) for s in segments)
        lost = sum((Counter(anchor.segment_digests) - have).values())
        message = (
            f"system prompt evicted (reason={reason}): {lost} of "
            f"{len(anchor.segment_digests)} anchored instruction segment(s) not found; "
            f"anchor sha256:{anchor.short}, {anchor.char_count} chars anchored, "
            f"{sum(len(s) for s in segments)} chars now"
        )
        return Diagnostic(None, "warning", SYSTEM_PROMPT_EVICTED, message)

    def _limit_for(self, model: str | None, explicit: int | None) -> int | None:
        if explicit is not None:
            return explicit if _count(explicit) else None
        if model is not None and model in self._limits:
            return self._limits[model]
        return self._default_limit

    def _capacity(
        self,
        request: Mapping[str, Any],
        model: str | None,
        usage: Mapping[str, Any] | None,
        prompt_tokens: int | None,
        reserved_completion: int | None,
        context_limit: int | None,
        findings: list[Diagnostic],
    ) -> dict[str, Any]:
        limit = self._limit_for(model, context_limit)
        prompt = _count(prompt_tokens) if prompt_tokens is not None else None
        if prompt is None:
            prompt = reported_prompt_tokens(usage)
        reserved = _count(reserved_completion) if reserved_completion is not None else None
        if reserved is None:
            reserved = reserved_completion_tokens(request)
        out: dict[str, Any] = {
            "context_limit": limit,
            "prompt_tokens": prompt,
            "reserved_completion": reserved if reserved is not None else 0,
            "reserved_completion_reported": reserved is not None,
        }
        if limit is None:
            findings.append(
                Diagnostic(
                    None,
                    "info",
                    CONTEXT_LIMIT_UNKNOWN,
                    "no context limit configured for this model; capacity not checked",
                )
            )
            return out
        if prompt is None:
            findings.append(
                Diagnostic(
                    None,
                    "info",
                    CONTEXT_USAGE_UNAVAILABLE,
                    "no reported prompt token count; capacity not checked (counts are never "
                    "estimated)",
                )
            )
            return out
        headroom = limit - (prompt + out["reserved_completion"])
        out["headroom_tokens"] = headroom
        out["headroom_percent"] = round(headroom * 100 / limit, 2)
        if headroom * 100 < limit * self._min_percent:
            findings.append(
                Diagnostic(
                    None,
                    "warning",
                    CONTEXT_WINDOW_EXHAUSTED,
                    f"context window nearly full: {headroom} of {limit} tokens left "
                    f"({out['headroom_percent']}%), below the {self._min_percent}% minimum "
                    f"(prompt {prompt} + reserved completion {out['reserved_completion']})",
                )
            )
        return out
