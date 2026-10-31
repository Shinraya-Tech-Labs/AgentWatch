"""Metric First-Order Temporal Logic (MFOTL) Engine with Open-World Quantifiers (v3.1 EvidenceLab).

Enterprise-grade first-order metric temporal logic verification for complex, parameterized
multi-agent behavioral invariants under open-world epistemic uncertainty.

Features:
- Full First-Order AST: Predicates, typed terms, comparisons, quantifiers (forall, exists).
- Metric Temporal Modalities: Past (Once, Historically, Since, Previous) and Future (Eventually, Always, Until, Next).
- Strong Kleene Three-Valued Valuation (True, False, Unknown) with open-world missingness preservation.
- Combinatoric Witness Slicing: Extracts minimal satisfying / falsifying event and artifact IDs.
- Resource Bounds: Polynomial join budgets with RESOURCE_EXCEEDED fail-closed semantics.
- Differential Testing Harness against reference closed-world monitors.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from agentwatch.contracts.schema import EvidenceVerdict
from agentwatch.events.model import ComputationalEvent

# ═══════════════════════════════════════════════════════════════════════════════
# 1. Epistemic 3-Valued Logic Algebra (Strong Kleene)
# ═══════════════════════════════════════════════════════════════════════════════

class TriValued(StrEnum):
    """Strong Kleene 3-valued epistemic truth value."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_bool(cls, val: bool) -> TriValued:
        return cls.TRUE if val else cls.FALSE

    def to_verdict(self) -> EvidenceVerdict:
        if self == TriValued.TRUE:
            return EvidenceVerdict.SUPPORTED
        elif self == TriValued.FALSE:
            return EvidenceVerdict.REFUTED
        return EvidenceVerdict.INCONCLUSIVE


def kleene_not(val: TriValued) -> TriValued:
    """Strong Kleene negation."""
    if val == TriValued.TRUE:
        return TriValued.FALSE
    elif val == TriValued.FALSE:
        return TriValued.TRUE
    return TriValued.UNKNOWN


def kleene_and(left: TriValued, right: TriValued) -> TriValued:
    """Strong Kleene conjunction: FALSE dominates; UNKNOWN propagates unless dominated."""
    if left == TriValued.FALSE or right == TriValued.FALSE:
        return TriValued.FALSE
    if left == TriValued.TRUE and right == TriValued.TRUE:
        return TriValued.TRUE
    return TriValued.UNKNOWN


def kleene_or(left: TriValued, right: TriValued) -> TriValued:
    """Strong Kleene disjunction: TRUE dominates; UNKNOWN propagates unless dominated."""
    if left == TriValued.TRUE or right == TriValued.TRUE:
        return TriValued.TRUE
    if left == TriValued.FALSE and right == TriValued.FALSE:
        return TriValued.FALSE
    return TriValued.UNKNOWN


def kleene_implies(antecedent: TriValued, consequent: TriValued) -> TriValued:
    """Strong Kleene implication: not A or B."""
    return kleene_or(kleene_not(antecedent), consequent)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Resource Bounding & Errors
# ═══════════════════════════════════════════════════════════════════════════════

class ResourceExceededError(RuntimeError):
    """Raised when evaluation exceeds configured memory or time cardinality budget."""
    pass


class MFOTLParseError(ValueError):
    """Raised when an MFOTL formula string cannot be parsed."""
    pass


@dataclass(frozen=True, slots=True)
class MFOTLBudget:
    """Complexity and resource guardrails for relational joins and trace evaluations."""

    max_join_tuples: int = 10_000
    max_trace_steps: int = 50_000
    max_quantifier_depth: int = 5
    fail_closed: bool = True  # If True, returns UNKNOWN with RESOURCE_EXCEEDED; else raises


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Combinatoric Witness Slicing
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class WitnessSlice:
    """Minimal proof / counter-example witness slice over event and artifact IDs."""

    value: TriValued
    event_ids: frozenset[str] = field(default_factory=frozenset)
    artifact_ids: frozenset[str] = field(default_factory=frozenset)
    bindings: tuple[tuple[str, Any], ...] = ()
    explanation: str = ""
    resource_exceeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.value.value,
            "evidence_verdict": self.value.to_verdict().value,
            "event_ids": sorted(self.event_ids),
            "artifact_ids": sorted(self.artifact_ids),
            "bindings": dict(self.bindings),
            "explanation": self.explanation,
            "resource_exceeded": self.resource_exceeded,
        }

    @classmethod
    def empty(cls, val: TriValued, explanation: str = "") -> WitnessSlice:
        return cls(value=val, explanation=explanation)

    @classmethod
    def resource_exhausted(cls, explanation: str) -> WitnessSlice:
        return cls(
            value=TriValued.UNKNOWN,
            explanation=explanation,
            resource_exceeded=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AST Representation
# ═══════════════════════════════════════════════════════════════════════════════

class Term(ABC):
    """Base class for first-order terms."""

    @abstractmethod
    def evaluate(self, env: dict[str, Any]) -> Any:
        """Resolve value under given variable assignment environment."""
        pass


@dataclass(frozen=True, slots=True)
class Var(Term):
    """Variable identifier term (e.g. x, amount)."""

    name: str

    def evaluate(self, env: dict[str, Any]) -> Any:
        return env.get(self.name)

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class Const(Term):
    """Literal constant term (numeric, string, boolean)."""

    value: Any

    def evaluate(self, env: dict[str, Any]) -> Any:
        return self.value

    def __str__(self) -> str:
        if isinstance(self.value, str):
            return f'"{self.value}"'
        return str(self.value)


@dataclass(frozen=True, slots=True)
class FieldAccess(Term):
    """Dot attribute access term (e.g. x.amount, y.limit)."""

    target: Term
    field_name: str

    def evaluate(self, env: dict[str, Any]) -> Any:
        obj = self.target.evaluate(env)
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(self.field_name)
        if hasattr(obj, self.field_name):
            val = getattr(obj, self.field_name)
            return val
        if hasattr(obj, "attributes") and isinstance(obj.attributes, dict):
            return obj.attributes.get(self.field_name)
        return None

    def __str__(self) -> str:
        return f"{self.target}.{self.field_name}"


class Formula(ABC):
    """Abstract base class for all MFOTL formulas."""

    @abstractmethod
    def free_variables(self) -> set[str]:
        """Set of unquantified free variables in the formula."""
        pass

    @abstractmethod
    def __str__(self) -> str:
        pass


@dataclass(frozen=True, slots=True)
class Predicate(Formula):
    """Atomic predicate relation (e.g. Transfer(x, amount))."""

    name: str
    args: tuple[Term, ...] = ()

    def free_variables(self) -> set[str]:
        free: set[str] = set()
        for arg in self.args:
            if isinstance(arg, Var):
                free.add(arg.name)
            elif isinstance(arg, FieldAccess) and isinstance(arg.target, Var):
                free.add(arg.target.name)
        return free

    def __str__(self) -> str:
        args_str = ", ".join(str(a) for a in self.args)
        return f"{self.name}({args_str})"


@dataclass(frozen=True, slots=True)
class Comparison(Formula):
    """Arithmetic/relational comparison formula (e.g. x.amount > 100)."""

    op: str
    left: Term
    right: Term

    def free_variables(self) -> set[str]:
        free: set[str] = set()
        for term in (self.left, self.right):
            if isinstance(term, Var):
                free.add(term.name)
            elif isinstance(term, FieldAccess) and isinstance(term.target, Var):
                free.add(term.target.name)
        return free

    def __str__(self) -> str:
        return f"{self.left} {self.op} {self.right}"


@dataclass(frozen=True, slots=True)
class Not(Formula):
    """Logical negation."""

    inner: Formula

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"not ({self.inner})"


@dataclass(frozen=True, slots=True)
class And(Formula):
    """Logical conjunction."""

    left: Formula
    right: Formula

    def free_variables(self) -> set[str]:
        return self.left.free_variables() | self.right.free_variables()

    def __str__(self) -> str:
        return f"({self.left} and {self.right})"


@dataclass(frozen=True, slots=True)
class Or(Formula):
    """Logical disjunction."""

    left: Formula
    right: Formula

    def free_variables(self) -> set[str]:
        return self.left.free_variables() | self.right.free_variables()

    def __str__(self) -> str:
        return f"({self.left} or {self.right})"


@dataclass(frozen=True, slots=True)
class Implies(Formula):
    """Logical implication (antecedent implies consequent)."""

    antecedent: Formula
    consequent: Formula

    def free_variables(self) -> set[str]:
        return self.antecedent.free_variables() | self.consequent.free_variables()

    def __str__(self) -> str:
        return f"({self.antecedent} implies {self.consequent})"


@dataclass(frozen=True, slots=True)
class Exists(Formula):
    """Existential quantifier: exists var. formula."""

    var: str
    formula: Formula

    def free_variables(self) -> set[str]:
        return self.formula.free_variables() - {self.var}

    def __str__(self) -> str:
        return f"(exists {self.var}. {self.formula})"


@dataclass(frozen=True, slots=True)
class Forall(Formula):
    """Universal quantifier: forall var. formula."""

    var: str
    formula: Formula

    def free_variables(self) -> set[str]:
        return self.formula.free_variables() - {self.var}

    def __str__(self) -> str:
        return f"(forall {self.var}. {self.formula})"


# ─────────────────────────────────────────────────────────────────────────────
# Temporal Operators (Metric Modalities)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class MetricInterval:
    """Bounded or unbounded metric time interval [lower_s, upper_s]."""

    lower: float = 0.0
    upper: float = float("inf")

    def contains(self, delta_seconds: float) -> bool:
        return self.lower <= delta_seconds <= self.upper

    def __str__(self) -> str:
        if math.isinf(self.upper):
            return f"[{self.lower}, inf]"
        return f"[{self.lower}, {self.upper}]"


@dataclass(frozen=True, slots=True)
class Previous(Formula):
    """Previous temporal operator: bullet_I phi (holds at previous step within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Previous{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Next(Formula):
    """Next temporal operator: circ_I phi (holds at next step within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Next{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Once(Formula):
    """Past diamond: Once[I] phi (holds sometime in past within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Once{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Historically(Formula):
    """Past box: Historically[I] phi (holds always in past within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Historically{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Eventually(Formula):
    """Future diamond: Eventually[I] phi (holds sometime in future within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Eventually{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Always(Formula):
    """Future box: Always[I] phi (holds globally in future within interval)."""

    inner: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.inner.free_variables()

    def __str__(self) -> str:
        return f"Always{self.interval} ({self.inner})"


@dataclass(frozen=True, slots=True)
class Since(Formula):
    """Past binary: phi Since[I] psi."""

    left: Formula
    right: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.left.free_variables() | self.right.free_variables()

    def __str__(self) -> str:
        return f"({self.left} Since{self.interval} {self.right})"


@dataclass(frozen=True, slots=True)
class Until(Formula):
    """Future binary: phi Until[I] psi."""

    left: Formula
    right: Formula
    interval: MetricInterval = field(default_factory=MetricInterval)

    def free_variables(self) -> set[str]:
        return self.left.free_variables() | self.right.free_variables()

    def __str__(self) -> str:
        return f"({self.left} Until{self.interval} {self.right})"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Formula Parser (Lexer + Recursive-Descent Parser)
# ═══════════════════════════════════════════════════════════════════════════════

_TOKEN_SPEC = [
    ("NUMBER", r"-?\d+(?:\.\d+)?"),
    ("STRING", r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\''),
    ("OP_COMP", r"==|!=|<=|>=|<|>"),
    ("DOT", r"\."),
    ("COMMA", r","),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("IDENT", r"[a-zA-Z_][a-zA-Z0-9_]*"),
    ("SKIP", r"[ \t\r\n]+"),
]

_TOKEN_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC))


@dataclass(frozen=True, slots=True)
class Token:
    type: str
    value: str
    pos: int


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _TOKEN_RE.finditer(text):
        kind = m.lastgroup
        val = m.group()
        if kind == "SKIP":
            continue
        tokens.append(Token(kind or "IDENT", val, m.start()))
    tokens.append(Token("EOF", "", len(text)))
    return tokens


class MFOTLParser:
    """Parser for textual Metric First-Order Temporal Logic expressions."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _tokenize(text)
        self.idx = 0

    def current(self) -> Token:
        return self.tokens[self.idx]

    def consume(self, expected_type: str | None = None, expected_val: str | None = None) -> Token:
        tok = self.current()
        if expected_type and tok.type != expected_type:
            raise MFOTLParseError(
                f"Expected token type '{expected_type}' but got '{tok.type}' ({tok.value}) at pos {tok.pos}"
            )
        if expected_val and tok.value.lower() != expected_val.lower():
            raise MFOTLParseError(
                f"Expected '{expected_val}' but got '{tok.value}' at pos {tok.pos}"
            )
        self.idx += 1
        return tok

    def match(self, tok_type: str, tok_val: str | None = None) -> bool:
        tok = self.current()
        if tok.type != tok_type:
            return False
        if tok_val is not None and tok.value.lower() != tok_val.lower():
            return False
        return True

    def parse(self) -> Formula:
        expr = self._parse_formula()
        if not self.match("EOF"):
            tok = self.current()
            raise MFOTLParseError(f"Unexpected trailing tokens starting at pos {tok.pos}: '{tok.value}'")
        return expr

    def _parse_formula(self) -> Formula:
        # Quantifiers: forall x. ... or exists x. ...
        if self.match("IDENT", "forall"):
            self.consume("IDENT", "forall")
            var_tok = self.consume("IDENT")
            self.consume("DOT")
            sub = self._parse_formula()
            return Forall(var_tok.value, sub)
        elif self.match("IDENT", "exists"):
            self.consume("IDENT", "exists")
            var_tok = self.consume("IDENT")
            self.consume("DOT")
            sub = self._parse_formula()
            return Exists(var_tok.value, sub)

        return self._parse_implies()

    def _parse_implies(self) -> Formula:
        left = self._parse_or()
        if self.match("IDENT", "implies") or self.match("IDENT", "->"):
            self.consume()
            right = self._parse_formula()
            return Implies(left, right)
        return left

    def _parse_or(self) -> Formula:
        left = self._parse_and()
        while self.match("IDENT", "or") or self.match("IDENT", "||"):
            self.consume()
            right = self._parse_and()
            left = Or(left, right)
        return left

    def _parse_and(self) -> Formula:
        left = self._parse_binary_temporal()
        while self.match("IDENT", "and") or self.match("IDENT", "&&"):
            self.consume()
            right = self._parse_binary_temporal()
            left = And(left, right)
        return left

    def _parse_binary_temporal(self) -> Formula:
        left = self._parse_unary()
        while True:
            if self.match("IDENT", "Since"):
                self.consume()
                interval = self._parse_optional_interval()
                right = self._parse_unary()
                left = Since(left, right, interval)
            elif self.match("IDENT", "Until"):
                self.consume()
                interval = self._parse_optional_interval()
                right = self._parse_unary()
                left = Until(left, right, interval)
            else:
                break
        return left

    def _parse_unary(self) -> Formula:
        if self.match("IDENT", "not") or self.match("IDENT", "!"):
            self.consume()
            return Not(self._parse_unary())

        temporal_ops = {
            "once": Once,
            "historically": Historically,
            "eventually": Eventually,
            "always": Always,
            "previous": Previous,
            "next": Next,
        }
        for name, cls in temporal_ops.items():
            if self.match("IDENT", name):
                self.consume()
                interval = self._parse_optional_interval()
                inner = self._parse_unary()
                return cls(inner, interval)

        return self._parse_primary()

    def _parse_optional_interval(self) -> MetricInterval:
        if not self.match("LBRACKET"):
            return MetricInterval()
        self.consume("LBRACKET")
        lower_tok = self.consume()
        lower = float(lower_tok.value)
        self.consume("COMMA")
        upper_tok = self.consume()
        upper = float("inf") if upper_tok.value.lower() in ("inf", "infinity") else float(upper_tok.value)
        self.consume("RBRACKET")
        return MetricInterval(lower, upper)

    def _parse_primary(self) -> Formula:
        if self.match("IDENT", "forall") or self.match("IDENT", "exists"):
            return self._parse_formula()

        if self.match("LPAREN"):
            self.consume("LPAREN")
            expr = self._parse_formula()
            self.consume("RPAREN")
            return expr

        # Could be predicate: Name(...)
        # Or comparison: term op term
        term1 = self._parse_term()
        if isinstance(term1, Var) and self.match("LPAREN"):
            # Predicate call
            self.consume("LPAREN")
            args: list[Term] = []
            if not self.match("RPAREN"):
                args.append(self._parse_term())
                while self.match("COMMA"):
                    self.consume("COMMA")
                    args.append(self._parse_term())
            self.consume("RPAREN")
            return Predicate(term1.name, tuple(args))

        if self.match("OP_COMP"):
            op_tok = self.consume("OP_COMP")
            term2 = self._parse_term()
            return Comparison(op_tok.value, term1, term2)

        # Standalone boolean predicate or variable
        if isinstance(term1, Var):
            return Predicate(term1.name, ())
        raise MFOTLParseError(f"Unexpected term in primary expression: {term1}")

    def _parse_term(self) -> Term:
        tok = self.current()
        if tok.type == "NUMBER":
            self.consume("NUMBER")
            val: int | float = float(tok.value)
            if val.is_integer():
                val = int(val)
            t: Term = Const(val)
        elif tok.type == "STRING":
            self.consume("STRING")
            val_str = tok.value[1:-1]  # strip quotes
            t = Const(val_str)
        elif tok.type == "IDENT":
            self.consume("IDENT")
            if tok.value.lower() == "true":
                t = Const(True)
            elif tok.value.lower() == "false":
                t = Const(False)
            elif tok.value.lower() == "null" or tok.value.lower() == "none":
                t = Const(None)
            else:
                t = Var(tok.value)
        else:
            raise MFOTLParseError(f"Expected term, got '{tok.value}' ({tok.type}) at pos {tok.pos}")

        # Field accesses: t.foo.bar
        while self.match("DOT"):
            self.consume("DOT")
            field_tok = self.consume("IDENT")
            t = FieldAccess(t, field_tok.value)

        return t


def parse_mfotl(formula_str: str) -> Formula:
    """Convenience helper to parse a string into an MFOTL AST formula."""
    return MFOTLParser(formula_str).parse()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Open-World Evaluation Context & Relation Store
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TracePoint:
    """A discrete temporal step in the trace."""

    index: int
    timestamp: float  # epoch seconds
    events: tuple[ComputationalEvent, ...]
    time_dt: datetime | None = None


class TraceDatabase:
    """In-memory indexed relational store over trace events for MFOTL monitoring."""

    def __init__(
        self,
        events: Sequence[ComputationalEvent],
        authoritative_predicates: set[str] | None = None,
        coverage_complete: bool = False,
    ) -> None:
        self.authoritative_predicates = set(authoritative_predicates or ())
        self.coverage_complete = coverage_complete

        # Sort events deterministically by timestamp
        def _get_ts(e: ComputationalEvent) -> float:
            if e.time.start:
                return e.time.start.timestamp()
            return 0.0

        sorted_events = sorted(events, key=_get_ts)

        # Group into temporal trace points
        ts_groups: dict[float, list[ComputationalEvent]] = defaultdict(list)
        for e in sorted_events:
            ts = _get_ts(e)
            ts_groups[ts].append(e)

        sorted_ts = sorted(ts_groups.keys())
        self.points: list[TracePoint] = []
        for i, ts in enumerate(sorted_ts):
            evs = ts_groups[ts]
            dt = evs[0].time.start if evs else None
            self.points.append(TracePoint(index=i, timestamp=ts, events=tuple(evs), time_dt=dt))

        # Index events by operation / name for fast predicate relational joins
        self.events_by_op: dict[str, list[ComputationalEvent]] = defaultdict(list)
        self.all_events = tuple(sorted_events)
        for e in sorted_events:
            self.events_by_op[e.operation].append(e)
            # Also index by kind if applicable
            if hasattr(e, "kind") and e.kind:
                self.events_by_op[e.kind.value].append(e)

    def is_predicate_complete(self, pred_name: str) -> bool:
        """Whether observation for this predicate is closed/authoritative."""
        if self.coverage_complete:
            return True
        return pred_name in self.authoritative_predicates


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Incremental & Trace-Based Relational MFOTL Monitor
# ═══════════════════════════════════════════════════════════════════════════════

class OpenWorldMFOTLMonitor:
    """Evaluates MFOTL formulas with strong 3-valued logic and open-world quantifiers."""

    def __init__(
        self,
        trace_db: TraceDatabase,
        budget: MFOTLBudget | None = None,
    ) -> None:
        self.trace_db = trace_db
        self.budget = budget or MFOTLBudget()
        self._join_counter = 0

    def evaluate(self, formula: Formula) -> WitnessSlice:
        """Evaluate formula over the entire trace database (default evaluated at final state)."""
        self._join_counter = 0
        if not self.trace_db.points:
            # Vacuous evaluation over empty trace
            return self._evaluate_empty(formula)

        last_idx = len(self.trace_db.points) - 1
        return self._eval(formula, last_idx, {})

    def evaluate_at(self, formula: Formula, step_idx: int) -> WitnessSlice:
        """Evaluate formula at a specific temporal step index."""
        self._join_counter = 0
        if not self.trace_db.points:
            return self._evaluate_empty(formula)
        step_idx = max(0, min(step_idx, len(self.trace_db.points) - 1))
        return self._eval(formula, step_idx, {})

    def _evaluate_empty(self, formula: Formula) -> WitnessSlice:
        if isinstance(formula, Forall):
            return WitnessSlice(
                value=TriValued.TRUE,
                explanation="Vacuous satisfaction of universal quantifier over empty trace",
            )
        elif isinstance(formula, Exists):
            val = TriValued.FALSE if self.trace_db.coverage_complete else TriValued.UNKNOWN
            return WitnessSlice(
                value=val,
                explanation="Empty trace cannot satisfy existential quantifier",
            )
        return WitnessSlice(value=TriValued.UNKNOWN, explanation="Empty trace")

    def _check_budget(self) -> None:
        self._join_counter += 1
        if self._join_counter > self.budget.max_join_tuples:
            msg = f"Join cardinality exceeded budget of {self.budget.max_join_tuples} tuples"
            if not self.budget.fail_closed:
                raise ResourceExceededError(msg)
            raise ResourceExceededError(msg)

    def _eval(self, formula: Formula, step: int, env: dict[str, Any]) -> WitnessSlice:
        try:
            self._check_budget()
        except ResourceExceededError as e:
            if not self.budget.fail_closed:
                raise
            return WitnessSlice.resource_exhausted(str(e))

        # ── Atomic Predicate ──────────────────────────────────────────────────
        if isinstance(formula, Predicate):
            return self._eval_predicate(formula, step, env)

        # ── Relational Comparison ─────────────────────────────────────────────
        elif isinstance(formula, Comparison):
            return self._eval_comparison(formula, env)

        # ── Boolean Connectives ───────────────────────────────────────────────
        elif isinstance(formula, Not):
            inner = self._eval(formula.inner, step, env)
            return WitnessSlice(
                value=kleene_not(inner.value),
                event_ids=inner.event_ids,
                artifact_ids=inner.artifact_ids,
                bindings=inner.bindings,
                explanation=f"not ({inner.explanation})",
                resource_exceeded=inner.resource_exceeded,
            )

        elif isinstance(formula, And):
            left = self._eval(formula.left, step, env)
            if left.resource_exceeded:
                return left
            if left.value == TriValued.FALSE:
                # Minimal counter-example: left conjunct already falsifies
                return left

            right = self._eval(formula.right, step, env)
            if right.resource_exceeded:
                return right
            if right.value == TriValued.FALSE:
                return right

            val = kleene_and(left.value, right.value)
            return WitnessSlice(
                value=val,
                event_ids=left.event_ids | right.event_ids,
                artifact_ids=left.artifact_ids | right.artifact_ids,
                bindings=tuple(dict(left.bindings + right.bindings).items()),
                explanation=f"({left.explanation} and {right.explanation})",
            )

        elif isinstance(formula, Or):
            left = self._eval(formula.left, step, env)
            if left.resource_exceeded:
                return left
            if left.value == TriValued.TRUE:
                # Minimal witness: left branch satisfies
                return left

            right = self._eval(formula.right, step, env)
            if right.resource_exceeded:
                return right
            if right.value == TriValued.TRUE:
                return right

            val = kleene_or(left.value, right.value)
            return WitnessSlice(
                value=val,
                event_ids=left.event_ids | right.event_ids,
                artifact_ids=left.artifact_ids | right.artifact_ids,
                bindings=tuple(dict(left.bindings + right.bindings).items()),
                explanation=f"({left.explanation} or {right.explanation})",
            )

        elif isinstance(formula, Implies):
            # A implies B === not A or B
            left = self._eval(formula.antecedent, step, env)
            if left.resource_exceeded:
                return left
            if left.value == TriValued.FALSE:
                # Antecedent false => implication vacuously satisfied!
                return WitnessSlice(
                    value=TriValued.TRUE,
                    event_ids=left.event_ids,
                    bindings=left.bindings,
                    explanation=f"Antecedent refuted ({left.explanation}); implication holds vacuously",
                )

            right = self._eval(formula.consequent, step, env)
            if right.resource_exceeded:
                return right

            val = kleene_implies(left.value, right.value)
            return WitnessSlice(
                value=val,
                event_ids=left.event_ids | right.event_ids,
                artifact_ids=left.artifact_ids | right.artifact_ids,
                bindings=tuple(dict(left.bindings + right.bindings).items()),
                explanation=f"({left.explanation} implies {right.explanation})",
            )

        # ── Quantifiers (Open-World Domain Iteration) ─────────────────────────
        elif isinstance(formula, Exists):
            return self._eval_exists(formula, step, env)

        elif isinstance(formula, Forall):
            return self._eval_forall(formula, step, env)

        # ── Past Metric Temporal Modalities ───────────────────────────────────
        elif isinstance(formula, Previous):
            if step <= 0:
                return WitnessSlice(value=TriValued.FALSE, explanation="No previous step exists")
            prev_ts = self.trace_db.points[step - 1].timestamp
            cur_ts = self.trace_db.points[step].timestamp
            delta = cur_ts - prev_ts
            if not formula.interval.contains(delta):
                return WitnessSlice(
                    value=TriValued.FALSE,
                    explanation=f"Delta {delta}s not in interval {formula.interval}",
                )
            return self._eval(formula.inner, step - 1, env)

        elif isinstance(formula, Once):
            return self._eval_once(formula, step, env)

        elif isinstance(formula, Historically):
            return self._eval_historically(formula, step, env)

        elif isinstance(formula, Since):
            return self._eval_since(formula, step, env)

        # ── Future Metric Temporal Modalities ─────────────────────────────────
        elif isinstance(formula, Next):
            if step >= len(self.trace_db.points) - 1:
                val = TriValued.FALSE if self.trace_db.coverage_complete else TriValued.UNKNOWN
                return WitnessSlice(value=val, explanation="No subsequent step in trace")
            next_ts = self.trace_db.points[step + 1].timestamp
            cur_ts = self.trace_db.points[step].timestamp
            delta = next_ts - cur_ts
            if not formula.interval.contains(delta):
                return WitnessSlice(
                    value=TriValued.FALSE,
                    explanation=f"Delta {delta}s not in interval {formula.interval}",
                )
            return self._eval(formula.inner, step + 1, env)

        elif isinstance(formula, Eventually):
            return self._eval_eventually(formula, step, env)

        elif isinstance(formula, Always):
            return self._eval_always(formula, step, env)

        elif isinstance(formula, Until):
            return self._eval_until(formula, step, env)

        raise NotImplementedError(f"Unsupported formula AST node: {type(formula).__name__}")

    # ── Predicate & Comparison Evaluation ─────────────────────────────────────

    def _extract_artifacts(self, ev: ComputationalEvent) -> frozenset[str]:
        arts: set[str] = set()
        for a in ev.inputs + ev.outputs:
            if hasattr(a, "artifact_id") and a.artifact_id:
                arts.add(a.artifact_id)
            elif hasattr(a, "digest") and a.digest:
                arts.add(a.digest)
        return frozenset(arts)

    def _eval_predicate(self, pred: Predicate, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_point = self.trace_db.points[step]
        # Match events at the current step matching pred.name
        matching_events = [e for e in cur_point.events if e.operation == pred.name]

        if not pred.args:
            if matching_events:
                ev = matching_events[0]
                artifacts = self._extract_artifacts(ev)
                return WitnessSlice(
                    value=TriValued.TRUE,
                    event_ids=frozenset([ev.event_id]),
                    artifact_ids=artifacts,
                    explanation=f"Predicate {pred.name} observed at step {step}",
                )
            val = TriValued.FALSE if self.trace_db.is_predicate_complete(pred.name) else TriValued.UNKNOWN
            return WitnessSlice(
                value=val,
                explanation=f"Predicate {pred.name} not observed at step {step}",
            )

        # Predicate has arguments: Transfer(x) or Transfer(x, amount)
        first_arg = pred.args[0]
        if isinstance(first_arg, Var):
            var_name = first_arg.name
            if var_name in env:
                # Variable already bound: verify that bound event matches
                bound_obj = env[var_name]
                if isinstance(bound_obj, ComputationalEvent):
                    if bound_obj.operation == pred.name and bound_obj.event_id in [e.event_id for e in cur_point.events]:
                        artifacts = self._extract_artifacts(bound_obj)
                        return WitnessSlice(
                            value=TriValued.TRUE,
                            event_ids=frozenset([bound_obj.event_id]),
                            artifact_ids=artifacts,
                            bindings=((var_name, bound_obj.event_id),),
                            explanation=f"Bound event {bound_obj.event_id} matches {pred.name}",
                        )
                    return WitnessSlice(
                        value=TriValued.FALSE,
                        explanation=f"Bound event {getattr(bound_obj, 'event_id', None)} does not match {pred.name} at step {step}",
                    )

        # Check against matching events at this step
        if matching_events:
            ev = matching_events[0]
            artifacts = self._extract_artifacts(ev)
            bindings: list[tuple[str, Any]] = []
            if isinstance(first_arg, Var):
                bindings.append((first_arg.name, ev.event_id))
            return WitnessSlice(
                value=TriValued.TRUE,
                event_ids=frozenset([ev.event_id]),
                artifact_ids=artifacts,
                bindings=tuple(bindings),
                explanation=f"Predicate {pred.name} satisfied by event {ev.event_id}",
            )

        val = TriValued.FALSE if self.trace_db.is_predicate_complete(pred.name) else TriValued.UNKNOWN
        return WitnessSlice(
            value=val,
            explanation=f"No matching event for {pred.name} at step {step}",
        )

    def _eval_comparison(self, comp: Comparison, env: dict[str, Any]) -> WitnessSlice:
        left_val = comp.left.evaluate(env)
        right_val = comp.right.evaluate(env)

        if left_val is None or right_val is None:
            if self.trace_db.coverage_complete:
                # Under closed world, absent attributes evaluate according to standard equality
                if comp.op == "==":
                    res = TriValued.TRUE if left_val == right_val else TriValued.FALSE
                    return WitnessSlice(value=res, explanation=f"Closed-world comparison {left_val} == {right_val}")
                elif comp.op == "!=":
                    res = TriValued.TRUE if left_val != right_val else TriValued.FALSE
                    return WitnessSlice(value=res, explanation=f"Closed-world comparison {left_val} != {right_val}")
                return WitnessSlice(value=TriValued.FALSE, explanation="Null in ordering comparison")
            # Under open world, missing attribute valuation is UNKNOWN
            return WitnessSlice(
                value=TriValued.UNKNOWN,
                explanation=f"Unresolved operand in comparison: {comp.left}={left_val}, {comp.right}={right_val}",
            )

        try:
            matched = False
            if comp.op == "==":
                matched = left_val == right_val
            elif comp.op == "!=":
                matched = left_val != right_val
            elif comp.op == "<":
                matched = left_val < right_val
            elif comp.op == "<=":
                matched = left_val <= right_val
            elif comp.op == ">":
                matched = left_val > right_val
            elif comp.op == ">=":
                matched = left_val >= right_val
            else:
                return WitnessSlice(value=TriValued.UNKNOWN, explanation=f"Unknown operator {comp.op}")

            res = TriValued.TRUE if matched else TriValued.FALSE
            return WitnessSlice(
                value=res,
                explanation=f"Comparison {left_val} {comp.op} {right_val} evaluated to {res.value}",
            )
        except Exception as e:
            return WitnessSlice(value=TriValued.UNKNOWN, explanation=f"Comparison evaluation error: {e}")

    # ── Quantifier Domain Expansion (Open-World Bounds) ────────────────────────

    def _get_domain(self, var_name: str) -> list[ComputationalEvent]:
        """Extract observed events as domain of valuation."""
        return list(self.trace_db.all_events)

    def _eval_exists(self, formula: Exists, step: int, env: dict[str, Any]) -> WitnessSlice:
        domain = self._get_domain(formula.var)
        saw_unknown = False
        unknown_reasons: list[str] = []

        for candidate in domain:
            new_env = dict(env)
            new_env[formula.var] = candidate
            res = self._eval(formula.formula, step, new_env)

            if res.resource_exceeded:
                return res
            if res.value == TriValued.TRUE:
                # Minimal satisfying witness: one satisfying assignment is sufficient
                return WitnessSlice(
                    value=TriValued.TRUE,
                    event_ids=res.event_ids | {candidate.event_id},
                    artifact_ids=res.artifact_ids,
                    bindings=((formula.var, candidate.event_id),) + res.bindings,
                    explanation=f"Existential quantifier satisfied by {formula.var}={candidate.event_id}: {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True
                unknown_reasons.append(res.explanation)

        # No observed candidate satisfied the formula.
        # Under open-world semantics: if domain/coverage is incomplete or any sub-evaluation was UNKNOWN,
        # we cannot collapse to FALSE!
        if saw_unknown or not self.trace_db.coverage_complete:
            return WitnessSlice(
                value=TriValued.UNKNOWN,
                explanation=(
                    f"Existential formula exists {formula.var} not satisfied by any observed event; "
                    f"open-world telemetry leaves existence UNKNOWN"
                ),
            )

        # Fully closed-world trace verified: all candidates evaluated to FALSE
        return WitnessSlice(
            value=TriValued.FALSE,
            explanation=f"Closed-world trace: all {len(domain)} candidates refuted exists {formula.var}",
        )

    def _eval_forall(self, formula: Forall, step: int, env: dict[str, Any]) -> WitnessSlice:
        domain = self._get_domain(formula.var)
        saw_unknown = False
        all_event_ids: set[str] = set()
        all_artifact_ids: set[str] = set()

        for candidate in domain:
            new_env = dict(env)
            new_env[formula.var] = candidate
            res = self._eval(formula.formula, step, new_env)

            if res.resource_exceeded:
                return res
            if res.value == TriValued.FALSE:
                # Minimal counter-example witness: one violating assignment is sufficient
                return WitnessSlice(
                    value=TriValued.FALSE,
                    event_ids=res.event_ids | {candidate.event_id},
                    artifact_ids=res.artifact_ids,
                    bindings=((formula.var, candidate.event_id),) + res.bindings,
                    explanation=f"Universal quantifier refuted by counter-example {formula.var}={candidate.event_id}: {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True
            elif res.value == TriValued.TRUE:
                all_event_ids |= res.event_ids | {candidate.event_id}
                all_artifact_ids |= res.artifact_ids

        if saw_unknown or not self.trace_db.coverage_complete:
            # In an open world, unobserved violating events could exist
            return WitnessSlice(
                value=TriValued.UNKNOWN,
                event_ids=frozenset(all_event_ids),
                artifact_ids=frozenset(all_artifact_ids),
                explanation=f"All observed candidates satisfied forall {formula.var}, but open-world bounds leave verdict UNKNOWN",
            )

        return WitnessSlice(
            value=TriValued.TRUE,
            event_ids=frozenset(all_event_ids),
            artifact_ids=frozenset(all_artifact_ids),
            explanation=f"Closed-world trace verified: all {len(domain)} candidates satisfied forall {formula.var}",
        )

    # ── Temporal Operators Evaluation ─────────────────────────────────────────

    def _eval_once(self, formula: Once, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp
        saw_unknown = False

        for j in range(step, -1, -1):
            past_ts = self.trace_db.points[j].timestamp
            delta = cur_ts - past_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            res = self._eval(formula.inner, j, env)
            if res.resource_exceeded:
                return res
            if res.value == TriValued.TRUE:
                return WitnessSlice(
                    value=TriValued.TRUE,
                    event_ids=res.event_ids,
                    artifact_ids=res.artifact_ids,
                    bindings=res.bindings,
                    explanation=f"Once{formula.interval} satisfied at past step {j} (delta={delta:.3f}s): {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True

        val = TriValued.UNKNOWN if saw_unknown or not self.trace_db.coverage_complete else TriValued.FALSE
        return WitnessSlice(value=val, explanation=f"Once{formula.interval} not observed in past window")

    def _eval_historically(self, formula: Historically, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp
        saw_unknown = False
        accum_events: set[str] = set()
        accum_artifacts: set[str] = set()

        for j in range(step, -1, -1):
            past_ts = self.trace_db.points[j].timestamp
            delta = cur_ts - past_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            res = self._eval(formula.inner, j, env)
            if res.resource_exceeded:
                return res
            if res.value == TriValued.FALSE:
                return WitnessSlice(
                    value=TriValued.FALSE,
                    event_ids=res.event_ids,
                    artifact_ids=res.artifact_ids,
                    bindings=res.bindings,
                    explanation=f"Historically{formula.interval} violated at past step {j}: {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True
            else:
                accum_events |= res.event_ids
                accum_artifacts |= res.artifact_ids

        val = TriValued.UNKNOWN if saw_unknown or not self.trace_db.coverage_complete else TriValued.TRUE
        return WitnessSlice(
            value=val,
            event_ids=frozenset(accum_events),
            artifact_ids=frozenset(accum_artifacts),
            explanation=f"Historically{formula.interval} evaluated across past window",
        )

    def _eval_since(self, formula: Since, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp

        # Search backwards for a step j where formula.right holds within interval
        for j in range(step, -1, -1):
            past_ts = self.trace_db.points[j].timestamp
            delta = cur_ts - past_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            right_res = self._eval(formula.right, j, env)
            if right_res.value == TriValued.TRUE:
                # Check that formula.left holds at all steps k strictly between j and step: j < k <= step
                left_ok = True
                saw_left_unknown = False
                left_events: set[str] = set()
                for k in range(j + 1, step + 1):
                    left_res = self._eval(formula.left, k, env)
                    if left_res.value == TriValued.FALSE:
                        left_ok = False
                        break
                    elif left_res.value == TriValued.UNKNOWN:
                        saw_left_unknown = True
                    left_events |= left_res.event_ids

                if left_ok and not saw_left_unknown:
                    return WitnessSlice(
                        value=TriValued.TRUE,
                        event_ids=right_res.event_ids | left_events,
                        artifact_ids=right_res.artifact_ids,
                        explanation=f"Since operator satisfied from step {j} to {step}",
                    )

        val = TriValued.UNKNOWN if not self.trace_db.coverage_complete else TriValued.FALSE
        return WitnessSlice(value=val, explanation="Since condition not satisfied")

    def _eval_eventually(self, formula: Eventually, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp
        saw_unknown = False

        for j in range(step, len(self.trace_db.points)):
            future_ts = self.trace_db.points[j].timestamp
            delta = future_ts - cur_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            res = self._eval(formula.inner, j, env)
            if res.resource_exceeded:
                return res
            if res.value == TriValued.TRUE:
                return WitnessSlice(
                    value=TriValued.TRUE,
                    event_ids=res.event_ids,
                    artifact_ids=res.artifact_ids,
                    bindings=res.bindings,
                    explanation=f"Eventually{formula.interval} satisfied at future step {j} (delta={delta:.3f}s): {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True

        val = TriValued.UNKNOWN if saw_unknown or not self.trace_db.coverage_complete else TriValued.FALSE
        return WitnessSlice(value=val, explanation=f"Eventually{formula.interval} not observed in future window")

    def _eval_always(self, formula: Always, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp
        saw_unknown = False
        accum_events: set[str] = set()
        accum_artifacts: set[str] = set()

        for j in range(step, len(self.trace_db.points)):
            future_ts = self.trace_db.points[j].timestamp
            delta = future_ts - cur_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            res = self._eval(formula.inner, j, env)
            if res.resource_exceeded:
                return res
            if res.value == TriValued.FALSE:
                return WitnessSlice(
                    value=TriValued.FALSE,
                    event_ids=res.event_ids,
                    artifact_ids=res.artifact_ids,
                    bindings=res.bindings,
                    explanation=f"Always{formula.interval} violated at future step {j}: {res.explanation}",
                )
            elif res.value == TriValued.UNKNOWN:
                saw_unknown = True
            else:
                accum_events |= res.event_ids
                accum_artifacts |= res.artifact_ids

        val = TriValued.UNKNOWN if saw_unknown or not self.trace_db.coverage_complete else TriValued.TRUE
        return WitnessSlice(
            value=val,
            event_ids=frozenset(accum_events),
            artifact_ids=frozenset(accum_artifacts),
            explanation=f"Always{formula.interval} evaluated across future window",
        )

    def _eval_until(self, formula: Until, step: int, env: dict[str, Any]) -> WitnessSlice:
        cur_ts = self.trace_db.points[step].timestamp

        for j in range(step, len(self.trace_db.points)):
            future_ts = self.trace_db.points[j].timestamp
            delta = future_ts - cur_ts
            if delta > formula.interval.upper:
                break
            if not formula.interval.contains(delta):
                continue

            right_res = self._eval(formula.right, j, env)
            if right_res.value == TriValued.TRUE:
                left_ok = True
                saw_left_unknown = False
                left_events: set[str] = set()
                for k in range(step, j):
                    left_res = self._eval(formula.left, k, env)
                    if left_res.value == TriValued.FALSE:
                        left_ok = False
                        break
                    elif left_res.value == TriValued.UNKNOWN:
                        saw_left_unknown = True
                    left_events |= left_res.event_ids

                if left_ok and not saw_left_unknown:
                    return WitnessSlice(
                        value=TriValued.TRUE,
                        event_ids=right_res.event_ids | left_events,
                        artifact_ids=right_res.artifact_ids,
                        explanation=f"Until operator satisfied from step {step} to {j}",
                    )

        val = TriValued.UNKNOWN if not self.trace_db.coverage_complete else TriValued.FALSE
        return WitnessSlice(value=val, explanation="Until condition not satisfied")


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Differential Reference Monitor (Standard 2-Valued Closed World)
# ═══════════════════════════════════════════════════════════════════════════════

class ReferenceMFOTLMonitor:
    """Standard closed-world reference monitor for differential testing.

    Evaluates formulas assuming strict closed-world semantics (no UNKNOWN state).
    Used to verify equivalence on complete traces.
    """

    def __init__(self, trace_db: TraceDatabase) -> None:
        self.trace_db = trace_db

    def evaluate(self, formula: Formula) -> bool:
        """Returns True if formula holds under closed-world semantics, False otherwise."""
        if not self.trace_db.points:
            return isinstance(formula, Forall)

        last_idx = len(self.trace_db.points) - 1
        return self._eval(formula, last_idx, {})

    def _eval(self, formula: Formula, step: int, env: dict[str, Any]) -> bool:
        if isinstance(formula, Predicate):
            point = self.trace_db.points[step]
            matching = [e for e in point.events if e.operation == formula.name]
            if not formula.args:
                return len(matching) > 0
            first_arg = formula.args[0]
            if isinstance(first_arg, Var) and first_arg.name in env:
                bound = env[first_arg.name]
                return any(
                    e.operation == formula.name and e.event_id == getattr(bound, "event_id", None)
                    for e in point.events
                )
            return len(matching) > 0

        elif isinstance(formula, Comparison):
            left_val = formula.left.evaluate(env)
            right_val = formula.right.evaluate(env)
            if left_val is None or right_val is None:
                return False
            if formula.op == "==":
                return bool(left_val == right_val)
            if formula.op == "!=":
                return bool(left_val != right_val)
            if formula.op == "<":
                return bool(left_val < right_val)
            if formula.op == "<=":
                return bool(left_val <= right_val)
            if formula.op == ">":
                return bool(left_val > right_val)
            if formula.op == ">=":
                return bool(left_val >= right_val)
            return False

        elif isinstance(formula, Not):
            return not self._eval(formula.inner, step, env)

        elif isinstance(formula, And):
            return self._eval(formula.left, step, env) and self._eval(formula.right, step, env)

        elif isinstance(formula, Or):
            return self._eval(formula.left, step, env) or self._eval(formula.right, step, env)

        elif isinstance(formula, Implies):
            if not self._eval(formula.antecedent, step, env):
                return True
            return self._eval(formula.consequent, step, env)

        elif isinstance(formula, Exists):
            for candidate in self.trace_db.all_events:
                new_env = dict(env)
                new_env[formula.var] = candidate
                if self._eval(formula.formula, step, new_env):
                    return True
            return False

        elif isinstance(formula, Forall):
            for candidate in self.trace_db.all_events:
                new_env = dict(env)
                new_env[formula.var] = candidate
                if not self._eval(formula.formula, step, new_env):
                    return False
            return True

        elif isinstance(formula, Once):
            cur_ts = self.trace_db.points[step].timestamp
            for j in range(step, -1, -1):
                delta = cur_ts - self.trace_db.points[j].timestamp
                if delta > formula.interval.upper:
                    break
                if formula.interval.contains(delta) and self._eval(formula.inner, j, env):
                    return True
            return False

        elif isinstance(formula, Historically):
            cur_ts = self.trace_db.points[step].timestamp
            for j in range(step, -1, -1):
                delta = cur_ts - self.trace_db.points[j].timestamp
                if delta > formula.interval.upper:
                    break
                if formula.interval.contains(delta) and not self._eval(formula.inner, j, env):
                    return False
            return True

        elif isinstance(formula, Eventually):
            cur_ts = self.trace_db.points[step].timestamp
            for j in range(step, len(self.trace_db.points)):
                delta = self.trace_db.points[j].timestamp - cur_ts
                if delta > formula.interval.upper:
                    break
                if formula.interval.contains(delta) and self._eval(formula.inner, j, env):
                    return True
            return False

        elif isinstance(formula, Always):
            cur_ts = self.trace_db.points[step].timestamp
            for j in range(step, len(self.trace_db.points)):
                delta = self.trace_db.points[j].timestamp - cur_ts
                if delta > formula.interval.upper:
                    break
                if formula.interval.contains(delta) and not self._eval(formula.inner, j, env):
                    return False
            return True

        return False
