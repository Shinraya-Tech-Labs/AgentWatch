"""Batch evaluation runner (issue #342).

Runs a user-supplied agent against a dataset of prompts and scores each run with
the existing :class:`ConfidenceScorer`, producing pass/fail rates, average
confidence, and hallucination-risk metrics across the batch.

Agent contract
--------------
The ``--agent`` script must expose a top-level callable::

    def run(prompt: str) -> list[AgentEvent]: ...

The runner imports the script, calls ``run`` once per prompt, and feeds the
returned events to the scorer. Everything here except :func:`load_agent` is pure
and side-effect free, which keeps the evaluation logic unit-testable.

Dataset format
--------------
A JSON array of cases::

    [{"id": "case-1", "prompt": "...", "expected": "optional substring"}, {"prompt": "..."}]

``id`` defaults to the 1-based index; ``expected``, when present, must appear in
the event trace for the case to pass (in addition to the confidence checks).
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from panoptis.core.schema import AgentEvent
from panoptis.scoring.confidence import ANOMALY_HALLUCINATED_SUCCESS, ConfidenceScorer

#: Signature of a loaded agent entry point.
AgentRunner = Callable[[str], list[AgentEvent]]

DEFAULT_PASS_THRESHOLD = 0.5


@dataclass
class EvalCase:
    """A single evaluation prompt."""

    id: str
    prompt: str
    expected: str | None = None


@dataclass
class EvalCaseResult:
    """The outcome of evaluating one case."""

    id: str
    passed: bool
    confidence: float
    hallucination_risk: float
    hallucinated: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "confidence": round(self.confidence, 4),
            "hallucination_risk": round(self.hallucination_risk, 4),
            "hallucinated": self.hallucinated,
            "error": self.error,
        }


@dataclass
class EvalReport:
    """Aggregate results across a batch."""

    threshold: float
    results: list[EvalCaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / max(1, self.total)

    @property
    def avg_confidence(self) -> float:
        return sum(r.confidence for r in self.results) / max(1, self.total)

    @property
    def avg_hallucination_risk(self) -> float:
        return sum(r.hallucination_risk for r in self.results) / max(1, self.total)

    @property
    def num_hallucinated(self) -> int:
        return sum(1 for r in self.results if r.hallucinated)

    @property
    def num_errored(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "pass_rate": round(self.pass_rate, 4),
                "avg_confidence": round(self.avg_confidence, 4),
                "avg_hallucination_risk": round(self.avg_hallucination_risk, 4),
                "num_hallucinated": self.num_hallucinated,
                "num_errored": self.num_errored,
            },
            "results": [r.to_dict() for r in self.results],
        }


def load_dataset(path: Path) -> list[EvalCase]:
    """Parse a dataset JSON file into :class:`EvalCase` objects.

    Raises:
        ValueError: If the JSON is malformed or a case is missing a prompt.
        OSError: If the file cannot be read.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"dataset {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("dataset must be a JSON array of cases")

    cases: list[EvalCase] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"dataset[{i}] must be an object")
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"dataset[{i}] is missing a non-empty 'prompt'")
        expected = item.get("expected")
        if expected is not None and not isinstance(expected, str):
            raise ValueError(f"dataset[{i}] 'expected' must be a string if present")
        cases.append(EvalCase(id=str(item.get("id", i + 1)), prompt=prompt, expected=expected))

    if not cases:
        raise ValueError("dataset is empty")
    return cases


def load_agent(path: Path) -> AgentRunner:
    """Import an agent script and return its ``run`` callable.

    Raises:
        FileNotFoundError: If the script does not exist.
        ImportError: If the script cannot be imported.
        AttributeError: If the script has no callable ``run``.
    """
    if not path.is_file():
        raise FileNotFoundError(f"agent script not found: {path}")
    spec = importlib.util.spec_from_file_location("panoptis_eval_agent", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load agent from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        raise AttributeError(
            f"agent script {path} must define a callable run(prompt) -> list[AgentEvent]"
        )
    return run  # type: ignore[no-any-return]


def _events_contain(events: list[AgentEvent], expected: str) -> bool:
    """Return True if ``expected`` appears (case-insensitively) in the event trace."""
    needle = expected.casefold()
    return any(needle in event.model_dump_json().casefold() for event in events)


def evaluate_case(
    case: EvalCase,
    agent_run: AgentRunner,
    scorer: ConfidenceScorer,
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
) -> EvalCaseResult:
    """Run one case through the agent and score it.

    A case passes when confidence is at least ``threshold``, no hallucinated-
    success anomaly is flagged, and — if the case declares ``expected`` — that
    text appears in the event trace. Any exception raised by the agent is caught
    and recorded as a failing, errored result.
    """
    try:
        events = agent_run(case.prompt)
    except Exception as exc:  # noqa: BLE001 - user code may raise anything
        return EvalCaseResult(
            id=case.id,
            passed=False,
            confidence=0.0,
            hallucination_risk=1.0,
            hallucinated=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(events, list) or not all(isinstance(e, AgentEvent) for e in events):
        return EvalCaseResult(
            id=case.id,
            passed=False,
            confidence=0.0,
            hallucination_risk=1.0,
            hallucinated=False,
            error="run(prompt) must return a list[AgentEvent]",
        )

    result = scorer.score(events, goal=case.prompt)
    confidence = result.overall_score
    halluc_component = result.component_scores.get("hallucinated_success", 1.0)
    hallucination_risk = max(0.0, 1.0 - halluc_component)
    hallucinated = ANOMALY_HALLUCINATED_SUCCESS in result.anomaly_flags
    expected_ok = case.expected is None or _events_contain(events, case.expected)
    passed = confidence >= threshold and not hallucinated and expected_ok

    return EvalCaseResult(
        id=case.id,
        passed=passed,
        confidence=confidence,
        hallucination_risk=hallucination_risk,
        hallucinated=hallucinated,
        error=None,
    )


def aggregate(results: list[EvalCaseResult], threshold: float) -> EvalReport:
    """Bundle per-case results into an :class:`EvalReport`."""
    return EvalReport(threshold=threshold, results=list(results))


def run_eval(
    dataset_path: Path,
    agent_path: Path,
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
) -> EvalReport:
    """Load a dataset and agent, evaluate every case sequentially, and aggregate."""
    cases = load_dataset(dataset_path)
    agent_run = load_agent(agent_path)
    scorer = ConfidenceScorer()
    results = [evaluate_case(case, agent_run, scorer, threshold=threshold) for case in cases]
    return aggregate(results, threshold)
