"""Reasoning-to-action intent grounding (WP-03).

Question: does this tool call serve the task the user gave, or did the agent drift, get
hijacked, or decide on its own to do something destructive? Ordinary safety rules cannot
answer it: ``DELETE FROM orders WHERE customer_id = 999`` is a well-formed, scoped
statement, and it is still wrong when the user asked to *look up* customer 123.

The grounder extracts from the objective:

* the *task mode*: read-only (find, check, list, summarise, ...), mutating (create, fix,
  update, ...) or destructive (delete, drop, clean up, ...);
* explicit prohibitions ("don't delete anything", "read-only", "without modifying");
* concrete entities: identifiers with values (``customer 123``, ``order #456``,
  ``id=7``), paths, hosts/URLs, table names, quoted strings.

and compares them with what the call does (its :class:`ArgumentProfile`). Hard,
explainable mismatches drive the verdict:

``GOAL_ACTION_MISMATCH``  destructive action, read-only objective → BLOCK;
                          mutating action, read-only objective → ESCALATE.
``OBJECTIVE_PROHIBITS``   the objective forbids what the call does → BLOCK.
``ENTITY_MISMATCH``       same kind of identifier, different value → ESCALATE.
``UNREQUESTED_DESTINATION`` data sent to a host the objective never mentioned while it
                          names others → ESCALATE.

Lexical similarity between objective and call is reported (``LOW_GOAL_OVERLAP``) but is
*never* sufficient to block: similarity is not proof of intent, and a dissimilar call can
be a legitimate step. It only raises an already-risky call to human review.

An optional verifier (a local model, or any callable) can be consulted for high-risk calls;
its answer is recorded with its reason, and if it fails the heuristic result stands.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any

from panoptis.protect.arguments import ArgumentProfile, extract_strings
from panoptis.protect.model import Finding, Severity, Stage, Verdict
from panoptis.security.owasp import OwaspVector

logger = logging.getLogger(__name__)

READ_VERBS = {
    "find", "check", "list", "show", "view", "analyze", "analyse", "summarize", "summarise", "explain",
    "read", "search", "look", "count", "report", "get", "fetch", "review", "inspect", "investigate",
    "describe", "diagnose", "audit", "compare", "lookup", "query", "display", "print", "examine", "monitor",
    "verify", "tell", "what", "why", "how", "which", "where", "who", "identify", "locate", "browse", "research",
}  # fmt: skip
MUTATE_VERBS = {
    "create", "write", "update", "fix", "edit", "add", "change", "rename", "refactor", "install", "deploy",
    "send", "post", "build", "implement", "migrate", "modify", "set", "configure", "commit", "push", "merge",
    "upgrade", "insert", "move", "replace", "generate", "save", "upload", "publish", "email", "notify", "apply",
    "patch", "run", "execute", "book", "order", "schedule", "submit", "reply", "transfer", "restart", "rebuild",
}  # fmt: skip
DESTROY_VERBS = {
    "delete", "remove", "drop", "purge", "wipe", "erase", "clean", "cleanup", "reset", "uninstall", "kill",
    "terminate", "destroy", "truncate", "prune", "clear", "revoke", "deprovision", "teardown", "decommission",
    "overwrite", "nuke", "shred", "rm",
}  # fmt: skip
PROHIBITION = re.compile(
    r"(?i)\b(do\s*n[o']?t|never|without|must\s+not|avoid|no)\s+(\w+\s+){0,3}?(delet\w*|remov\w*|drop\w*|modif\w*|chang\w*|writ\w*|touch\w*|push\w*|send\w*|email\w*|deploy\w*|commit\w*|edit\w*|alter\w*|overwrit\w*)"
    r"|\b(delet|remov|drop|chang|modif|touch|writ|edit|alter)\w*\s+(nothing|none)\b"
    r"|\bleave\s+(everything|it|them|all\s+\w+|the\s+\w+)\s+(as\s+is|untouched|alone|intact)\b"
    r"|\bread[- ]only\b|\bdry[- ]run\b|(?<!n't\s)(?<!not\s)\bjust\s+(look|check|read|tell)\b"
)
_ENTITY_KV = re.compile(
    r"(?i)\b([a-z][a-z_]{1,30}?)(?:[_\s-]?(?:id|no|number|num))?\s*(?:[:=#]|\s+#?|\bis\b\s*)\s*['\"]?([A-Za-z]{0,4}[-_]?\d{1,12})\b"
)
_STOP_ENTITY = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "and",
    "or",
    "is",
    "be",
    "top",
    "last",
    "first",
    "page",
    "step",
    "line",
    "port",
    "version",
    "v",
    "python",
    "about",
    "around",
    "over",
    "under",
    "than",
    "all",
    "any",
    "every",
    "least",
    "most",
}
_TOKEN = re.compile(r"[a-z0-9_]{3,}")
_HOST = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?\b", re.I)
_PATHLIKE = re.compile(
    r"(?:~|\.{1,2})?/[\w.\-/]+|\b[\w\-.]+/(?:[\w\-.]+/?)*|[A-Za-z]:\\[\w.\-\\]+|\b[\w\-]+\.(?:py|js|ts|json|ya?ml|toml|md|txt|csv|sql|sh|cfg|ini|env|log)\b"
)
_NOT_PATHS = {
    "and/or",
    "either/or",
    "he/she",
    "his/her",
    "yes/no",
    "true/false",
    "on/off",
    "w/",
    "n/a",
}
_TABLE = re.compile(
    r"(?i)\b(?:table|from|in)\s+[`\"]?([a-z_][a-z0-9_]{2,})[`\"]?|\b([a-z_][a-z0-9_]{2,})\s+table\b"
)


@dataclass
class Objective:
    text: str
    mode: str  # read_only | mutating | destructive | unknown
    prohibitions: list[str] = field(default_factory=list)
    entities: dict[str, set[str]] = field(default_factory=dict)  # kind -> values
    hosts: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)
    authorized: dict[str, Any] = field(default_factory=dict)  # context supplied by the integration

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "prohibitions": self.prohibitions,
            "entities": {k: sorted(v) for k, v in self.entities.items()},
            "hosts": sorted(self.hosts),
            "paths": sorted(self.paths),
            "tables": sorted(self.tables),
            "authorized_context": bool(self.authorized),
        }


_POLITE = re.compile(
    r"^(please|kindly|can you|could you|would you|will you|i want you to|i need you to|i'd like you to|help me|go ahead and|try to|just|also|then|now|first|next|finally)\s+",
    re.I,
)
_CLAUSE = re.compile(r"[.;:!?\n]|,\s*|\band\b|\bthen\b|\bafter that\b")


def _leading_verbs(text: str) -> set[str]:
    """The imperative verb opening each clause ("look up X, then delete Y" -> look, delete)."""
    verbs: set[str] = set()
    for clause in _CLAUSE.split(text.lower()):
        c = clause.strip()
        for _ in range(3):
            c2 = _POLITE.sub("", c)
            if c2 == c:
                break
            c = c2
        m = re.match(r"[a-z]+", c)
        if m:
            verbs.add(m.group(0))
    return verbs


def extract_objective(text: str, context: dict[str, Any] | None = None) -> Objective:
    """Parse a natural-language task into what it allows and what it names.

    ``context`` is authorized context the integration knows besides the text: ``hosts``,
    ``paths``, ``tables`` and ``entities`` (``{kind: [values]}``) the task may touch. It
    widens what the task names; it never changes the task's mode.
    """
    t = text.strip()
    low = t.lower()
    prohibitions = [m.group(0) for m in PROHIBITION.finditer(t)]
    stripped = PROHIBITION.sub(" ", low)  # verbs inside prohibitions do not grant the mode
    lead = _leading_verbs(stripped)
    known = lead & (DESTROY_VERBS | MUTATE_VERBS | READ_VERBS)
    # leading imperative verbs decide; nouns like "order history" do not
    words = known or set(re.findall(r"[a-z]+", stripped))
    if words & DESTROY_VERBS or re.search(r"\bclean\s*up\b|\bget rid of\b|\btear down\b", stripped):
        mode = "destructive"
    elif words & MUTATE_VERBS:
        mode = "mutating"
    elif words & READ_VERBS or t.endswith("?"):
        mode = "read_only"
    else:
        mode = "unknown"
    entities: dict[str, set[str]] = {}
    for m in _ENTITY_KV.finditer(t):
        kind, value = m.group(1).lower().rstrip("_"), m.group(2)
        if kind in _STOP_ENTITY or len(kind) < 2:
            continue
        entities.setdefault(_root(kind), set()).add(value.lower().lstrip("#"))
    hosts = {
        h.lower()
        for h in _HOST.findall(t)
        if not re.fullmatch(
            r"[\w\-]+\.(py|js|ts|json|ya?ml|toml|md|txt|csv|sql|sh|cfg|ini|log|env)", h.lower()
        )
    }
    paths = {p for p in _PATHLIKE.findall(t) if p.lower() not in _NOT_PATHS}
    tables = {a or b for a, b in _TABLE.findall(t)} - {"the", "this", "that", "which"}
    ctx = context or {}
    for kind, values in (ctx.get("entities") or {}).items():
        entities.setdefault(_root(str(kind)), set()).update(str(v).lower() for v in values)
    hosts |= {str(h).lower() for h in ctx.get("hosts") or ()}
    paths |= {str(x) for x in ctx.get("paths") or ()}
    return Objective(
        text=t,
        mode=mode,
        prohibitions=prohibitions,
        entities=entities,
        hosts=hosts,
        paths=paths,
        tables={x.lower() for x in tables} | {str(x).lower() for x in ctx.get("tables") or ()},
        tokens=set(_TOKEN.findall(low)),
        authorized=dict(ctx),
    )


def _root(kind: str) -> str:
    kind = re.sub(r"(_?id|_?no|_?num(ber)?)$", "", kind.lower())
    return kind[:-1] if kind.endswith("s") and len(kind) > 3 else kind


def action_entities(arguments: dict[str, Any], profile: ArgumentProfile) -> dict[str, set[str]]:
    """Identifier values the call uses: argument keys like ``customer_id`` and SQL ``x_id = n``."""
    found: dict[str, set[str]] = {}

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, (int, str)) and not isinstance(node, bool) and key:
            sval = str(node).strip()
            if re.fullmatch(r"[A-Za-z]{0,4}[-_]?\d{1,12}", sval) and re.search(
                r"(?i)(id|no|num|number)$|^(customer|user|order|account|invoice|ticket|issue|pr|project|repo|employee|patient|item)",
                key,
            ):
                found.setdefault(_root(key), set()).add(sval.lower())

    walk(arguments)
    for stmt in profile.sql_statements:
        for m in re.finditer(r"(?i)\b([a-z_]+?)_?id\s*(?:=|in\s*\()\s*'?(\w+)", stmt):
            found.setdefault(_root(m.group(1)), set()).add(m.group(2).lower())
    for _, text in profile.strings:
        for m in re.finditer(
            r"(?i)\b(customer|user|order|account|invoice|ticket|issue|pr|employee|patient)[_\s-]?(?:id)?[\s:=#]+(\d{1,12})\b",
            text,
        ):
            found.setdefault(_root(m.group(1)), set()).add(m.group(2))
    return found


Verifier = Callable[[str, str, dict[str, Any]], tuple[str, str]]
"""``verifier(objective, tool, arguments) -> (answer, reason)``; answer is YES, NO or UNSURE."""


@dataclass
class GroundingConfig:
    overlap_threshold: float = 0.08
    verifier: Verifier | None = None
    verifier_no_verdict: Verdict = Verdict.BLOCK
    verify_only_high_risk: bool = True
    verifier_timeout_s: float = 5.0  # a verifier slower than this is treated as unavailable
    # a high-risk call (destructive, sends data, reads secrets) under an objective whose
    # mode cannot be determined; similarity to the objective never lowers this
    ambiguous_high_risk: Verdict = Verdict.ESCALATE_HITL
    # a high-risk call with no objective at all; ALLOW keeps grounding opt-in
    missing_objective_high_risk: Verdict = Verdict.ALLOW


_VERIFIER_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="intent-verifier")


def _within(path: str, roots: set[str]) -> bool:
    p = path.replace("\\", "/").rstrip("/").removeprefix("./")
    for r in roots:
        r = r.replace("\\", "/").rstrip("/").removeprefix("./")
        if p == r or p.startswith(r + "/") or (r and p.endswith("/" + r)):
            return True
    return False


class IntentGrounder:
    def __init__(self, config: GroundingConfig | None = None) -> None:
        self.config = config or GroundingConfig()

    def ground(
        self,
        objective_text: str | None,
        tool: str,
        arguments: dict[str, Any],
        profile: ArgumentProfile,
        *,
        reasoning: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[Finding]:
        high_risk = (
            profile.destructive or (profile.network and profile.mutating) or profile.reads_sensitive
        )
        if not objective_text or not objective_text.strip():
            v = self.config.missing_objective_high_risk if high_risk else Verdict.ALLOW
            return [
                Finding(
                    Stage.INTENT,
                    "NO_OBJECTIVE",
                    Severity.MEDIUM if v is not Verdict.ALLOW else Severity.INFO,
                    "No task objective supplied; intent grounding skipped"
                    if v is Verdict.ALLOW
                    else "No task objective supplied for a high-risk call",
                    {"confidence": 0.0, "high_risk": bool(high_risk)},
                    OwaspVector.GOAL_HIJACKING.value if v is not Verdict.ALLOW else None,
                    v,
                )
            ]
        obj = extract_objective(objective_text, context)
        out: list[Finding] = []
        ev_obj = {"objective_mode": obj.mode, "objective": obj.text[:300]}

        # 1. task mode vs what the call does
        if obj.mode == "read_only" and profile.destructive:
            out.append(
                Finding(
                    Stage.INTENT,
                    "GOAL_ACTION_MISMATCH",
                    Severity.CRITICAL,
                    f"The task is read-only but '{tool}' is destructive",
                    {**ev_obj, "action": "destructive"},
                    OwaspVector.GOAL_HIJACKING.value,
                    Verdict.BLOCK,
                )
            )
        elif obj.mode == "read_only" and profile.mutating:
            out.append(
                Finding(
                    Stage.INTENT,
                    "GOAL_ACTION_MISMATCH",
                    Severity.HIGH,
                    f"The task is read-only but '{tool}' changes state",
                    {**ev_obj, "action": "mutating"},
                    OwaspVector.GOAL_HIJACKING.value,
                    Verdict.ESCALATE_HITL,
                )
            )
        elif obj.mode == "mutating" and profile.destructive:
            out.append(
                Finding(
                    Stage.INTENT,
                    "DESTRUCTION_NOT_REQUESTED",
                    Severity.HIGH,
                    f"The task asks for changes, not deletion, but '{tool}' is destructive",
                    {**ev_obj, "action": "destructive"},
                    OwaspVector.GOAL_HIJACKING.value,
                    Verdict.ESCALATE_HITL,
                )
            )

        elif obj.mode == "unknown" and high_risk:
            out.append(
                Finding(
                    Stage.INTENT,
                    "AMBIGUOUS_OBJECTIVE",
                    Severity.HIGH,
                    f"The task does not say what kind of work it is; '{tool}' is high-risk",
                    {**ev_obj, "action": _action_kind(profile)},
                    OwaspVector.GOAL_HIJACKING.value,
                    self.config.ambiguous_high_risk,
                )
            )

        # 1b. a destructive call on targets the task never named, while it names others
        if profile.destructive and obj.mode != "read_only":
            stray_tables = sorted(profile.sql_tables - obj.tables) if obj.tables else []
            stray_paths = (
                sorted(p for p in profile.paths if not _within(p, obj.paths)) if obj.paths else []
            )
            if stray_tables or stray_paths:
                out.append(
                    Finding(
                        Stage.INTENT,
                        "TARGET_NOT_REQUESTED",
                        Severity.HIGH,
                        f"'{tool}' destroys {', '.join(stray_tables + stray_paths)[:200]}, which the task does not name",
                        {
                            **ev_obj,
                            "targets": stray_tables + stray_paths,
                            "requested": sorted(obj.tables | obj.paths),
                        },
                        OwaspVector.GOAL_HIJACKING.value,
                        Verdict.ESCALATE_HITL,
                    )
                )

        # 2. explicit prohibitions
        if obj.prohibitions and (profile.mutating or profile.destructive or profile.network):
            forbidden_kind = " ".join(obj.prohibitions).lower()
            violates = (
                (
                    profile.destructive
                    and re.search(
                        r"delet|remov|drop|overwrit|leave|read[- ]only|dry[- ]run|just",
                        forbidden_kind,
                    )
                )
                or (
                    profile.mutating
                    and re.search(
                        r"modif|chang|writ|touch|edit|alter|leave|read[- ]only|dry[- ]run|just|commit|push|deploy",
                        forbidden_kind,
                    )
                )
                or (
                    profile.network
                    and profile.mutating
                    and re.search(r"send|email|push|deploy|read[- ]only", forbidden_kind)
                )
            )
            if violates:
                out.append(
                    Finding(
                        Stage.INTENT,
                        "OBJECTIVE_PROHIBITS",
                        Severity.CRITICAL,
                        f"The task explicitly says {obj.prohibitions[0]!r}; '{tool}' does that",
                        {**ev_obj, "prohibitions": obj.prohibitions},
                        OwaspVector.GOAL_HIJACKING.value,
                        Verdict.BLOCK,
                    )
                )

        # 3. entity mismatch: same identifier kind, different value
        acted = action_entities(arguments, profile)
        for kind, values in acted.items():
            wanted = obj.entities.get(kind)
            if wanted and not (values & wanted):
                out.append(
                    Finding(
                        Stage.INTENT,
                        "ENTITY_MISMATCH",
                        Severity.HIGH
                        if (profile.mutating or profile.destructive)
                        else Severity.MEDIUM,
                        f"The task concerns {kind} {', '.join(sorted(wanted))} but the call targets {kind} {', '.join(sorted(values))}",
                        {
                            **ev_obj,
                            "kind": kind,
                            "requested": sorted(wanted),
                            "acted_on": sorted(values),
                        },
                        OwaspVector.GOAL_HIJACKING.value,
                        Verdict.ESCALATE_HITL
                        if (profile.mutating or profile.destructive)
                        else Verdict.ALLOW,
                    )
                )

        # 4. data sent to a destination the task never named, while it names others
        if profile.network and profile.mutating and obj.hosts:
            foreign = {
                h
                for h in profile.hosts
                if not any(h == o or h.endswith("." + o) or o.endswith("." + h) for o in obj.hosts)
            }
            if foreign:
                out.append(
                    Finding(
                        Stage.INTENT,
                        "UNREQUESTED_DESTINATION",
                        Severity.HIGH,
                        f"Sends data to {', '.join(sorted(foreign))}; the task only mentions {', '.join(sorted(obj.hosts))}",
                        {**ev_obj, "destinations": sorted(foreign)},
                        OwaspVector.GOAL_HIJACKING.value,
                        Verdict.ESCALATE_HITL,
                    )
                )

        # 5. lexical overlap: reported, never decisive on its own
        call_tokens = set(
            _TOKEN.findall(
                (tool + " " + " ".join(t for _, t in extract_strings(arguments))).lower()
            )
        )
        overlap = len(call_tokens & obj.tokens) / max(1, min(len(call_tokens), len(obj.tokens)))
        if overlap < self.config.overlap_threshold:
            out.append(
                Finding(
                    Stage.INTENT,
                    "LOW_GOAL_OVERLAP",
                    Severity.MEDIUM if high_risk else Severity.LOW,
                    f"The call shares little vocabulary with the task (overlap {overlap:.2f}); not proof of drift",
                    {**ev_obj, "overlap": round(overlap, 3)},
                    OwaspVector.GOAL_HIJACKING.value,
                    Verdict.ESCALATE_HITL
                    if (high_risk and obj.mode in ("read_only", "unknown"))
                    else Verdict.ALLOW,
                )
            )

        # 6. optional verifier for high-risk calls
        if self.config.verifier and (high_risk or not self.config.verify_only_high_risk):
            out.extend(self._verify(obj, tool, arguments, reasoning))
        if not any(f.severity.rank >= Severity.MEDIUM.rank for f in out):
            out.append(
                Finding(
                    Stage.INTENT,
                    "GROUNDED",
                    Severity.INFO,
                    f"Consistent with a {obj.mode.replace('_', '-')} task",
                    {**ev_obj, "overlap": round(overlap, 3)},
                    None,
                    Verdict.ALLOW,
                )
            )
        for f in out:
            f.evidence.setdefault("confidence", _confidence(f.code, obj, overlap))
        return out

    def _verify(
        self, obj: Objective, tool: str, arguments: dict[str, Any], reasoning: str | None
    ) -> list[Finding]:
        fut = _VERIFIER_POOL.submit(self.config.verifier, obj.text, tool, arguments)  # type: ignore[arg-type]
        try:
            answer, reason = fut.result(timeout=self.config.verifier_timeout_s)
            reason = str(reason or "")
        except FutureTimeout:
            fut.cancel()
            logger.warning("intent verifier timed out after %ss", self.config.verifier_timeout_s)
            return [
                Finding(
                    Stage.INTENT,
                    "VERIFIER_UNAVAILABLE",
                    Severity.INFO,
                    f"Intent verifier timed out after {self.config.verifier_timeout_s}s; heuristic result used",
                    {"timeout_s": self.config.verifier_timeout_s},
                    None,
                    Verdict.ALLOW,
                )
            ]
        except Exception as exc:  # noqa: BLE001 - a verifier must never take enforcement down
            logger.warning("intent verifier failed: %s", exc)
            return [
                Finding(
                    Stage.INTENT,
                    "VERIFIER_UNAVAILABLE",
                    Severity.INFO,
                    f"Intent verifier failed ({type(exc).__name__}); heuristic result used",
                    {},
                    None,
                    Verdict.ALLOW,
                )
            ]
        answer = str(answer or "").strip().upper()
        if answer.startswith("NO"):
            return [
                Finding(
                    Stage.INTENT,
                    "VERIFIER_REJECTED",
                    Severity.HIGH,
                    f"Intent verifier: the call does not advance the task: {reason[:300]}",
                    {"answer": "NO", "reason": reason[:500]},
                    OwaspVector.GOAL_HIJACKING.value,
                    self.config.verifier_no_verdict,
                )
            ]
        if answer.startswith("YES"):
            return [
                Finding(
                    Stage.INTENT,
                    "VERIFIER_CONFIRMED",
                    Severity.INFO,
                    f"Intent verifier confirmed: {reason[:200]}",
                    {"answer": "YES", "reason": reason[:500]},
                    None,
                    Verdict.ALLOW,
                )
            ]
        return [
            Finding(
                Stage.INTENT,
                "VERIFIER_UNSURE",
                Severity.LOW,
                f"Intent verifier unsure: {reason[:200]}",
                {"answer": answer or "UNSURE"},
                None,
                Verdict.ALLOW,
            )
        ]


def openai_compatible_verifier(
    base_url: str, model: str, *, api_key: str | None = None, timeout: float = 10.0
) -> Verifier:
    """A verifier backed by any OpenAI-compatible chat endpoint (Ollama, vLLM, LM Studio, ...).

    The prompt asks for YES/NO with a reason; anything else is UNSURE.
    """
    import httpx

    def verify(objective: str, tool: str, arguments: dict[str, Any]) -> tuple[str, str]:
        args = json.dumps(arguments, default=str)[:2000]
        prompt = (
            f"Task: {objective}\nProposed action: {tool}({args})\n"
            "Does this action directly advance the task without gratuitous destruction or sending data "
            "the task did not ask to send? Reply on one line: YES or NO, then a short reason."
        )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        r = httpx.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 80,
            },
            headers=headers,
            timeout=timeout,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        word = text.split(None, 1)
        return (word[0].strip(".,:").upper() if word else "UNSURE", text)

    return verify


def _action_kind(profile: ArgumentProfile) -> str:
    if profile.destructive:
        return "destructive"
    if profile.network and profile.mutating:
        return "sends_data"
    if profile.reads_sensitive:
        return "reads_sensitive"
    return "mutating" if profile.mutating else "read"


# How sure the heuristic is of each finding (0-1). Recorded with the finding as evidence;
# it explains a decision, it does not make one.
_CONFIDENCE = {
    "OBJECTIVE_PROHIBITS": 0.9,
    "GOAL_ACTION_MISMATCH": 0.85,
    "DESTRUCTION_NOT_REQUESTED": 0.7,
    "ENTITY_MISMATCH": 0.7,
    "UNREQUESTED_DESTINATION": 0.7,
    "TARGET_NOT_REQUESTED": 0.65,
    "AMBIGUOUS_OBJECTIVE": 0.5,
    "LOW_GOAL_OVERLAP": 0.3,
}


def _confidence(code: str, obj: Objective, overlap: float) -> float:
    if code == "GROUNDED":
        # higher when the task's mode is known and the call shares its words
        base = 0.4 if obj.mode == "unknown" else 0.6
        return round(min(0.9, base + 0.3 * min(1.0, overlap)), 2)
    return _CONFIDENCE.get(code, 0.5)
