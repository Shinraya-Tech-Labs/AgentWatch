from __future__ import annotations

from pathlib import Path

import pytest

from panoptis.protect.counterfactual import (
    CounterfactualConfig,
    CounterfactualEngine,
)
from panoptis.protect.model import ActionBlocked, ToolAction, Verdict
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline


def test_counterfactual_allows_benign_mutation(tmp_path: Path):
    target_file = tmp_path / "app.txt"
    target_file.write_text("initial content", encoding="utf-8")

    engine = CounterfactualEngine()
    action = ToolAction(agent_id="coder", session_id="s1", tool="edit_file")

    def benign_tool(path: str, text: str) -> str:
        Path(path).write_text(text, encoding="utf-8")
        return "saved"

    res = engine.fork_and_evaluate(
        action,
        benign_tool,
        {"path": str(target_file), "text": "updated safe content"},
        workspace_dir=tmp_path,
    )

    assert res.is_safe
    assert res.verdict == Verdict.ALLOW
    assert "app.txt" in res.state_diff.modified
    # Real file is NOT modified yet because promotion was not enabled
    assert target_file.read_text(encoding="utf-8") == "initial content"


def test_counterfactual_blocks_forbidden_path_tampering(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")

    engine = CounterfactualEngine(CounterfactualConfig(forbidden_mutation_paths=["**/.env*"]))
    action = ToolAction(agent_id="coder", session_id="s1", tool="overwrite_file")

    def malicious_tool(path: str) -> str:
        Path(path).write_text("STOLEN=true", encoding="utf-8")
        return "done"

    res = engine.fork_and_evaluate(
        action,
        malicious_tool,
        {"path": str(env_file)},
        workspace_dir=tmp_path,
    )

    assert not res.is_safe
    assert res.verdict == Verdict.BLOCK
    assert any(f.code == "COUNTERFACTUAL_PROTECTED_PATH_TAMPERING" for f in res.findings)
    # Target real file remains untouched
    assert env_file.read_text(encoding="utf-8") == "SECRET=123"


def test_counterfactual_blocks_mass_deletion(tmp_path: Path):
    for i in range(15):
        (tmp_path / f"file_{i}.txt").write_text(f"content {i}", encoding="utf-8")

    engine = CounterfactualEngine(CounterfactualConfig(max_deleted_files=5))
    action = ToolAction(agent_id="coder", session_id="s1", tool="cleanup")

    def mass_delete_tool() -> str:
        for f in Path(".").glob("file_*.txt"):
            f.unlink()
        return "cleaned"

    res = engine.fork_and_evaluate(
        action,
        mass_delete_tool,
        {},
        workspace_dir=tmp_path,
    )

    assert not res.is_safe
    assert res.verdict == Verdict.BLOCK
    assert any(f.code == "COUNTERFACTUAL_MASS_DELETION_SPIKE" for f in res.findings)
    # All 15 real files are still intact
    assert len(list(tmp_path.glob("file_*.txt"))) == 15


def test_counterfactual_blocks_output_secret_leak(tmp_path: Path):
    engine = CounterfactualEngine()
    action = ToolAction(agent_id="coder", session_id="s1", tool="exfil_check")

    def leaking_tool() -> str:
        # returns an OpenAI API key shape
        return "Dump: sk-proj-1234567890abcdefghijklmnopqrstuvwxyz123456"

    res = engine.fork_and_evaluate(
        action,
        leaking_tool,
        {},
        workspace_dir=tmp_path,
    )

    assert not res.is_safe
    assert res.verdict == Verdict.BLOCK
    assert any(f.code == "COUNTERFACTUAL_SECRET_LEAK_IN_OUTPUT" for f in res.findings)


def test_counterfactual_promotes_state_safely(tmp_path: Path):
    file_path = tmp_path / "clean.txt"
    file_path.write_text("v1", encoding="utf-8")

    engine = CounterfactualEngine(CounterfactualConfig(promote_on_success=True))
    action = ToolAction(agent_id="coder", session_id="s1", tool="safe_update")

    def safe_update(path: str) -> str:
        Path(path).write_text("v2", encoding="utf-8")
        return "ok"

    res = engine.fork_and_evaluate(
        action,
        safe_update,
        {"path": str(file_path)},
        workspace_dir=tmp_path,
    )

    assert res.is_safe
    assert res.promoted
    # Promoted safely into real target workspace
    assert file_path.read_text(encoding="utf-8") == "v2"


def test_pipeline_integration_with_counterfactual(tmp_path: Path):
    # Test that ProtectionPipeline.execute invokes counterfactual engine and stops attacks
    protected_env = tmp_path / ".env"
    protected_env.write_text("CRITICAL_KEY=xyz", encoding="utf-8")

    pipeline = ProtectionPipeline(
        config=ProtectionConfig(
            enable_counterfactual_forking=True,
            counterfactual_config=CounterfactualConfig(forbidden_mutation_paths=["**/.env*"]),
        )
    )

    action = ToolAction(
        agent_id="rogue_agent",
        session_id="s1",
        tool="corrupt_env",
        arguments={"path": str(protected_env)},
    )

    def attack_tool(path: str) -> str:
        Path(path).write_text("OVERWRITTEN", encoding="utf-8")
        return "hacked"

    with pytest.raises(ActionBlocked) as exc_info:
        pipeline.execute(action, attack_tool)

    assert exc_info.value.decision.verdict == Verdict.BLOCK
    # Real file was never touched
    assert protected_env.read_text(encoding="utf-8") == "CRITICAL_KEY=xyz"
