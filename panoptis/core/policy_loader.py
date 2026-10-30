"""Discover, load, scaffold, and apply local safety-policy files (issue #340).

A repository can carry a ``.agentwatch-safety.yml`` (the ``policy_dsl`` ``rules:``
schema) to customize how commands are treated by ``panoptis safety check``.
This module provides:

* discovery — cwd first, then ``~/.agentwatch`` — via :func:`discover_policy_file`;
* loading via :func:`load_policy_engine` (delegates to ``PolicyEngine.from_yaml``);
* a scaffold template (:data:`DEFAULT_POLICY_YAML`) for ``safety config generate``;
* a pure, testable :func:`combine` that overlays a custom policy decision on a
  builtin risk verdict.

The combine step deliberately lives here (pure) rather than in the security-
critical ``SafetyEngine._evaluate_safety`` runtime path, so ``safety check`` can
honor a local policy without changing runtime enforcement behavior.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from panoptis.core.policy_dsl import PolicyAction, PolicyDecision, PolicyEngine
from panoptis.core.schema import RiskLevel

#: Conventional filename for a repo-local safety policy.
POLICY_FILENAME = ".agentwatch-safety.yml"

#: Scaffold written by ``safety config generate``. Matches the policy_dsl schema.
DEFAULT_POLICY_YAML = """\
# AgentWatch safety policy — customize how commands are treated in this repo.
# Rules are evaluated top-to-bottom; the first matching rule wins.
# Conditions may reference: tool, command, confidence, risk, framework.
# Actions: allow | block | require_approval | pause_and_alert | log_only
#
# Precedence notes:
#   * `block` forces a command to be treated as blocked.
#   * `allow` permits a command that builtin heuristics would otherwise flag,
#     EXCEPT commands that are hard-blocked as CRITICAL (e.g. `rm -rf /`) — those
#     can never be allowed away.
rules:
  - if: command contains "rm -rf"
    then: block
    label: block-recursive-force-rm
  - if: command contains "docker-compose down"
    then: allow
    label: allow-compose-down
"""


def config_dir() -> Path:
    """Return the AgentWatch config directory (``~/.agentwatch`` by default)."""
    override = os.getenv("AGENTWATCH_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".agentwatch"


def discover_policy_file(start_dir: Path | None = None) -> Path | None:
    """Locate the active policy file.

    Precedence: a repo-local ``<start_dir>/.agentwatch-safety.yml`` wins over a
    global ``~/.agentwatch/.agentwatch-safety.yml``.

    Args:
        start_dir: Directory to treat as the repo root (defaults to cwd).

    Returns:
        The path to the policy file, or ``None`` if neither location has one.
    """
    start = start_dir or Path.cwd()
    local = start / POLICY_FILENAME
    if local.is_file():
        return local
    global_file = config_dir() / POLICY_FILENAME
    if global_file.is_file():
        return global_file
    return None


def load_policy_engine(path: Path) -> PolicyEngine:
    """Load a :class:`PolicyEngine` from a policy file.

    Raises:
        ValueError: If the file is not valid YAML or fails schema validation.
        OSError: If the file cannot be read.
    """
    text = path.read_text(encoding="utf-8")
    try:
        return PolicyEngine.from_yaml(text)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize parser errors (e.g. yaml.YAMLError)
        raise ValueError(f"invalid policy file {path}: {exc}") from exc


class PolicyOutcome(str, Enum):
    """The effect a local policy has on a builtin risk verdict."""

    BLOCKED = "blocked"
    REQUIRES_APPROVAL = "requires_approval"
    ALLOWED = "allowed"
    UNCHANGED = "unchanged"  # no matching custom rule, or a no-op action


@dataclass
class CombinedVerdict:
    """Result of overlaying a policy decision on a builtin risk verdict."""

    outcome: PolicyOutcome
    effective_risk: RiskLevel
    matched_label: str | None
    note: str | None = None


def combine(risk_level: RiskLevel, decision: PolicyDecision) -> CombinedVerdict:
    """Overlay a custom :class:`PolicyDecision` on a builtin risk verdict.

    Semantics:
        * No matching custom rule -> ``UNCHANGED`` (builtin verdict stands).
        * ``block`` / ``pause_and_alert`` -> ``BLOCKED``.
        * ``require_approval`` -> ``REQUIRES_APPROVAL``.
        * ``allow`` -> ``ALLOWED``, unless the builtin verdict is ``CRITICAL`` (a
          hard block such as ``rm -rf /``), which can never be allowed away — that
          stays ``UNCHANGED`` with an explanatory note.
        * ``log_only`` -> ``UNCHANGED`` (builtin verdict stands).

    Args:
        risk_level: The builtin risk level for the command.
        decision: The decision returned by ``PolicyEngine.evaluate``.

    Returns:
        A :class:`CombinedVerdict` describing the effective outcome.
    """
    rule = decision.matched_rule
    if rule is None:
        return CombinedVerdict(PolicyOutcome.UNCHANGED, risk_level, None)

    label = rule.label or rule.condition
    action = decision.action

    if action in (PolicyAction.BLOCK, PolicyAction.PAUSE_AND_ALERT):
        return CombinedVerdict(PolicyOutcome.BLOCKED, RiskLevel.CRITICAL, label)
    if action == PolicyAction.REQUIRE_APPROVAL:
        return CombinedVerdict(PolicyOutcome.REQUIRES_APPROVAL, risk_level, label)
    if action == PolicyAction.ALLOW:
        if risk_level == RiskLevel.CRITICAL:
            return CombinedVerdict(
                PolicyOutcome.UNCHANGED,
                RiskLevel.CRITICAL,
                label,
                note="allow rule ignored: CRITICAL commands cannot be allowed away",
            )
        return CombinedVerdict(
            PolicyOutcome.ALLOWED,
            RiskLevel.SAFE,
            label,
            note=f"builtin risk {risk_level.value} permitted by local policy",
        )
    # log_only (and any future non-blocking action): builtin verdict stands.
    return CombinedVerdict(PolicyOutcome.UNCHANGED, risk_level, label)
