"""Tests for local safety-policy discovery, loading, and combination (issue #340)."""

from __future__ import annotations

from pathlib import Path

import pytest

from panoptis.core.policy_dsl import PolicyAction, PolicyDecision, Rule
from panoptis.core.policy_loader import (
    DEFAULT_POLICY_YAML,
    POLICY_FILENAME,
    CombinedVerdict,
    PolicyOutcome,
    combine,
    discover_policy_file,
    load_policy_engine,
)
from panoptis.core.schema import AgentEvent, EventType, RiskLevel, ToolCallData

# ── discovery ──────────────────────────────────────────────────────────────


def test_discover_prefers_repo_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / POLICY_FILENAME).write_text("rules: []", encoding="utf-8")
    monkeypatch.setenv("AGENTWATCH_CONFIG_DIR", str(global_dir))

    repo = tmp_path / "repo"
    repo.mkdir()
    local = repo / POLICY_FILENAME
    local.write_text("rules: []", encoding="utf-8")

    assert discover_policy_file(repo) == local


def test_discover_falls_back_to_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    gfile = global_dir / POLICY_FILENAME
    gfile.write_text("rules: []", encoding="utf-8")
    monkeypatch.setenv("AGENTWATCH_CONFIG_DIR", str(global_dir))

    repo = tmp_path / "repo"
    repo.mkdir()  # no local file

    assert discover_policy_file(repo) == gfile


def test_discover_returns_none_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTWATCH_CONFIG_DIR", str(tmp_path / "empty"))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert discover_policy_file(repo) is None


# ── loading ────────────────────────────────────────────────────────────────


def test_default_scaffold_loads_and_is_valid(tmp_path: Path) -> None:
    f = tmp_path / POLICY_FILENAME
    f.write_text(DEFAULT_POLICY_YAML, encoding="utf-8")
    engine = load_policy_engine(f)
    labels = {r.label for r in engine.rules}
    assert "block-recursive-force-rm" in labels
    assert "allow-compose-down" in labels


def test_load_invalid_policy_raises(tmp_path: Path) -> None:
    f = tmp_path / POLICY_FILENAME
    f.write_text("not_rules: []", encoding="utf-8")  # missing required 'rules'
    with pytest.raises(ValueError):
        load_policy_engine(f)


def test_load_malformed_yaml_raises_valueerror(tmp_path: Path) -> None:
    # A YAML syntax error must surface as ValueError (not a raw yaml.YAMLError),
    # so the CLI's ValueError/OSError handling covers bad policy files.
    f = tmp_path / POLICY_FILENAME
    f.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy_engine(f)


# ── combine (the ALLOW-override semantics) ────────────────────────────────


def _decision(action: PolicyAction | None) -> PolicyDecision:
    if action is None:
        return PolicyDecision(matched_rule=None, action=PolicyAction.ALLOW)
    return PolicyDecision(matched_rule=Rule("cond", action, "lbl"), action=action)


def test_combine_no_rule_is_unchanged() -> None:
    v = combine(RiskLevel.HIGH, _decision(None))
    assert v.outcome is PolicyOutcome.UNCHANGED
    assert v.effective_risk is RiskLevel.HIGH
    assert v.matched_label is None


def test_combine_block_blocks() -> None:
    v = combine(RiskLevel.SAFE, _decision(PolicyAction.BLOCK))
    assert v.outcome is PolicyOutcome.BLOCKED
    assert v.effective_risk is RiskLevel.CRITICAL
    assert v.matched_label == "lbl"


def test_combine_pause_and_alert_blocks() -> None:
    v = combine(RiskLevel.SAFE, _decision(PolicyAction.PAUSE_AND_ALERT))
    assert v.outcome is PolicyOutcome.BLOCKED


def test_combine_require_approval() -> None:
    v = combine(RiskLevel.MEDIUM, _decision(PolicyAction.REQUIRE_APPROVAL))
    assert v.outcome is PolicyOutcome.REQUIRES_APPROVAL
    assert v.effective_risk is RiskLevel.MEDIUM


def test_combine_allow_permits_soft_risk() -> None:
    v = combine(RiskLevel.HIGH, _decision(PolicyAction.ALLOW))
    assert v.outcome is PolicyOutcome.ALLOWED
    assert v.effective_risk is RiskLevel.SAFE


def test_combine_allow_cannot_override_critical() -> None:
    v = combine(RiskLevel.CRITICAL, _decision(PolicyAction.ALLOW))
    assert v.outcome is PolicyOutcome.UNCHANGED
    assert v.effective_risk is RiskLevel.CRITICAL
    assert v.note is not None  # explains why the allow was ignored


def test_combine_log_only_is_unchanged() -> None:
    v = combine(RiskLevel.MEDIUM, _decision(PolicyAction.LOG_ONLY))
    assert v.outcome is PolicyOutcome.UNCHANGED
    assert v.effective_risk is RiskLevel.MEDIUM


# ── end-to-end: the issue's own example ───────────────────────────────────


def _verdict_for(engine, cmd: str, builtin: RiskLevel) -> CombinedVerdict:
    event = AgentEvent(
        session_id="s",
        agent_id="a",
        event_type=EventType.TOOL_CALL,
        tool_call=ToolCallData(tool_name="bash", raw_command=cmd, arguments={"command": cmd}),
    )
    return combine(builtin, engine.evaluate(event))


def test_scaffold_blocks_rm_rf_and_allows_compose_down(tmp_path: Path) -> None:
    f = tmp_path / POLICY_FILENAME
    f.write_text(DEFAULT_POLICY_YAML, encoding="utf-8")
    engine = load_policy_engine(f)

    # rm -rf / is CRITICAL in builtins; the block rule keeps it blocked.
    assert _verdict_for(engine, "rm -rf /", RiskLevel.CRITICAL).outcome is PolicyOutcome.BLOCKED
    # docker-compose down is SAFE in builtins; the allow rule marks it allowed.
    assert (
        _verdict_for(engine, "docker-compose down", RiskLevel.SAFE).outcome is PolicyOutcome.ALLOWED
    )
