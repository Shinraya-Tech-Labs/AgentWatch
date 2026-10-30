"""
SAF-005 — Agent Policy DSL.

Human-readable YAML rules. Runtime evaluation. Example:

    rules:
      - if: tool == "bash" and command contains "rm"
        then: require_approval
      - if: confidence < 0.5
        then: pause_and_alert
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from panoptis.core.schema import AgentEvent

logger = logging.getLogger(__name__)


class PolicyAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    PAUSE_AND_ALERT = "pause_and_alert"
    LOG_ONLY = "log_only"


@dataclass
class Rule:
    condition: str
    action: PolicyAction
    label: str = ""


@dataclass
class PolicyDecision:
    matched_rule: Rule | None
    action: PolicyAction
    reasons: list[str] = field(default_factory=list)


# ── Mini-expression evaluator (subset, safe) ──────────────────────────────


class _Eval:
    """
    Safe minimal evaluator for the DSL. Supports:
        identifiers: tool, command, confidence, risk, tag, args
        operators:   ==  !=  <  <=  >  >=  contains  startswith  endswith
        boolean:     and  or  not
        parentheses
    """

    _TOKEN = re.compile(
        r"""
        \s*(?:
          (?P<NUM>\d+(?:\.\d+)?)
        | (?P<STR>"[^"]*"|'[^']*')
        | (?P<ID>[A-Za-z_][A-Za-z0-9_.]*)
        | (?P<OP><=|>=|==|!=|<|>|=)
        | (?P<PAREN>[()])
        )
        """,
        re.VERBOSE,
    )

    KEYWORDS = {"and", "or", "not", "contains", "startswith", "endswith"}

    def __init__(self, expr: str, env: dict[str, Any]):
        self.expr = expr
        self.env = env
        self.tokens: list[tuple[str, str]] = []
        self.pos = 0
        self._tokenize()

    def _tokenize(self) -> None:
        pos = 0
        while pos < len(self.expr):
            m = self._TOKEN.match(self.expr, pos)
            if not m:
                raise ValueError(f"bad token at {pos}: {self.expr[pos : pos + 20]!r}")
            kind = m.lastgroup
            if kind is None:  # unreachable: every alternative is a named group
                raise ValueError(f"unmatched token at {pos}")
            text = m.group(kind)
            self.tokens.append((kind, text))
            pos = m.end()

    def _peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> tuple[str, str]:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Any:
        return self._or()

    def _or(self) -> Any:
        v = self._and()
        while (t := self._peek()) and t[0] == "ID" and t[1] == "or":
            self._next()
            right = self._and()
            v = v or right
        return v

    def _and(self) -> Any:
        v = self._not()
        while (t := self._peek()) and t[0] == "ID" and t[1] == "and":
            self._next()
            right = self._not()
            v = v and right
        return v

    def _not(self) -> Any:
        t = self._peek()
        if t and t[0] == "ID" and t[1] == "not":
            self._next()
            return not self._not()
        return self._cmp()

    def _cmp(self) -> Any:
        left = self._atom()
        t = self._peek()
        if t is None:
            return left
        if t[0] == "OP" or (t[0] == "ID" and t[1] in ("contains", "startswith", "endswith")):
            self._next()
            right = self._atom()
            op = t[1]
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == "<":
                return float(left) < float(right)
            if op == "<=":
                return float(left) <= float(right)
            if op == ">":
                return float(left) > float(right)
            if op == ">=":
                return float(left) >= float(right)
            if op == "contains":
                return str(right) in str(left)
            if op == "startswith":
                return str(left).startswith(str(right))
            if op == "endswith":
                return str(left).endswith(str(right))
        return left

    def _atom(self) -> Any:
        t = self._next()
        if t[0] == "NUM":
            return float(t[1])
        if t[0] == "STR":
            return t[1][1:-1]
        if t[0] == "PAREN" and t[1] == "(":
            v = self._or()
            nxt = self._peek()
            if not (nxt and nxt[1] == ")"):
                raise ValueError("expected closing paren")
            self._next()
            return v
        if t[0] == "ID":
            if t[1] in ("true", "True"):
                return True
            if t[1] in ("false", "False"):
                return False
            if t[1] in self.KEYWORDS:
                raise ValueError(f"unexpected keyword {t[1]}")
            return self._lookup(t[1])
        raise ValueError(f"unexpected token {t}")

    def _lookup(self, name: str) -> Any:
        parts = name.split(".")
        cur: Any = self.env
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                cur = getattr(cur, p, None)
            if cur is None:
                return None
        return cur


# ── Engine ────────────────────────────────────────────────────────────────


class PolicyEngine:
    """Compile YAML rules and evaluate them against events."""

    def __init__(self, rules: list[Rule] | None = None):
        self.rules: list[Rule] = list(rules or [])

    @classmethod
    def from_yaml(cls, text: str) -> PolicyEngine:
        try:
            import yaml  # noqa: PLC0415

            data = yaml.safe_load(text)
        except ImportError:
            # Minimal fallback parser — rules: list of {if, then}
            data = _mini_yaml(text)

        # Validate data strictly before parsing rules
        validate_policy_dict(data)

        rules: list[Rule] = []
        for item in (data or {}).get("rules", []):
            cond = item.get("if", "true")
            action = PolicyAction(item.get("then", "log_only"))
            label = item.get("label", "")
            rules.append(Rule(condition=cond, action=action, label=label))
        return cls(rules)

    def evaluate(self, event: AgentEvent) -> PolicyDecision:
        env = self._env_for(event)
        for rule in self.rules:
            try:
                ok = _Eval(rule.condition, env).parse()
            except Exception as exc:  # noqa: BLE001
                logger.warning("policy_dsl: bad rule %r: %s", rule.condition, exc)
                continue
            if ok:
                return PolicyDecision(
                    matched_rule=rule,
                    action=rule.action,
                    reasons=[f"rule_matched:{rule.label or rule.condition}"],
                )
        return PolicyDecision(matched_rule=None, action=PolicyAction.ALLOW)

    def _env_for(self, event: AgentEvent) -> dict[str, Any]:
        env: dict[str, Any] = {
            "tool": event.tool_call.tool_name if event.tool_call else "",
            "command": (event.tool_call.raw_command if event.tool_call else "") or "",
            "args": event.tool_call.arguments if event.tool_call else {},
            "confidence": event.confidence.overall_score if event.confidence else 1.0,
            "risk": event.safety.risk_score if event.safety else 0.0,
            "framework": event.framework.value,
            "step": event.step_number,
        }
        return env


def _mini_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML fallback parser for `rules: [...]` shape only."""
    rules: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("- "):
            if cur:
                rules.append(cur)
            cur = {}
            line = line[2:]
        m = re.match(r"\s*(\w+)\s*:\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            cur[key] = val
    if cur:
        rules.append(cur)
    # remove the top "rules" line if it accidentally came in
    rules = [r for r in rules if "if" in r or "then" in r]
    return {"rules": rules}


def policy_config_schema() -> dict[str, Any]:
    """Return the JSON Schema document for the safety policy config."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "SafetyPolicyConfig",
        "description": "Schema for validating panoptis safety policy files.",
        "type": "object",
        "required": ["rules"],
        "additionalProperties": False,
        "properties": {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["if", "then"],
                    "additionalProperties": False,
                    "properties": {
                        "if": {"type": "string"},
                        "then": {
                            "type": "string",
                            "enum": [
                                "allow",
                                "block",
                                "require_approval",
                                "pause_and_alert",
                                "log_only",
                            ],
                        },
                        "label": {"type": "string"},
                    },
                },
            }
        },
    }


def validate_policy_dict(data: Any) -> None:
    """Validate a loaded safety policy dictionary against the strict JSON schema rules."""
    if not isinstance(data, dict):
        raise ValueError("Invalid safety policy: root must be a dictionary")

    # Check required fields
    if "rules" not in data:
        raise ValueError("Invalid safety policy: missing required property 'rules'")

    # Check for additional properties at root
    allowed_root_keys = {"rules"}
    extra_root_keys = set(data.keys()) - allowed_root_keys
    if extra_root_keys:
        raise ValueError(
            f"Invalid safety policy: additional properties not allowed: {sorted(list(extra_root_keys))}"
        )

    rules = data["rules"]
    if not isinstance(rules, list):
        raise ValueError("Invalid safety policy: 'rules' property must be an array")

    valid_actions = {action.value for action in PolicyAction}

    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ValueError(f"Invalid safety policy: rules[{idx}] must be an object")

        # Check additional properties in rule
        allowed_rule_keys = {"if", "then", "label"}
        extra_rule_keys = set(rule.keys()) - allowed_rule_keys
        if extra_rule_keys:
            raise ValueError(
                f"Invalid safety policy: rules[{idx}] additional properties not allowed: {sorted(list(extra_rule_keys))}"
            )

        # Check required fields in rule
        if "if" not in rule:
            raise ValueError(f"Invalid safety policy: rules[{idx}] missing required property 'if'")
        if "then" not in rule:
            raise ValueError(
                f"Invalid safety policy: rules[{idx}] missing required property 'then'"
            )

        # Check for property types and values
        if not isinstance(rule["if"], str):
            raise ValueError(f"Invalid safety policy: rules[{idx}].if must be a string")
        if not isinstance(rule["then"], str):
            raise ValueError(f"Invalid safety policy: rules[{idx}].then must be a string")
        if rule["then"] not in valid_actions:
            raise ValueError(
                f"Invalid safety policy: rules[{idx}].then must be one of {sorted(list(valid_actions))}, got '{rule['then']}'"
            )

        if "label" in rule:
            if not isinstance(rule["label"], str):
                raise ValueError(f"Invalid safety policy: rules[{idx}].label must be a string")


__all__ = ["PolicyEngine", "Rule", "PolicyAction", "PolicyDecision", "policy_config_schema"]
