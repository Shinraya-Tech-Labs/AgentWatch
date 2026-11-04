"""Unit tests for the Panopti CLI command center."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from panopti.attest import AttestationEngine
from panopti.cli import main
from panopti.court import WitnessChain
from panopti.replay import ReplayTape
from panoptis.cli._utils import ensure_utf8_stdio


def test_cli_doctor_command(capsys: pytest.CaptureFixture[str]) -> None:
    """panopti doctor prints diagnostic report and banner."""
    ret = main(["doctor"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "PANOPTI RUNTIME DOCTOR" in captured
    assert "Overall Status" in captured


def test_cli_live_command(capsys: pytest.CaptureFixture[str]) -> None:
    """panopti top / live prints sentinel monitor."""
    ret = main(["live"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "PANOPTI SENTINEL [LIVE MONITOR" in captured


def test_cli_court_verify_valid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """panopti court verify confirms valid cryptographic proof."""
    chain = WitnessChain("test-cli-court")
    chain.record("agent", "action_1", {"key": "val"})
    proof = chain.export_proof()

    proof_file = tmp_path / "test_proof.json"
    proof_file.write_text(json.dumps(proof), encoding="utf-8")

    ret = main(["court", "verify", str(proof_file)])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "VERIFIED: Witness proof" in captured


def test_cli_attest_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """panopti attest verify confirms signed attestation seal."""
    key = "cli-test-key-" + "x" * 32  # at least 32 bytes; not a published key
    seal = AttestationEngine.create_seal(
        merkle_root="abc123merkle",
        tool_count=3,
        confidence_floor=0.95,
        secret_key=key,
    )
    seal_file = tmp_path / "seal.json"
    seal_file.write_text(seal.to_json(), encoding="utf-8")

    ret = main(["attest", "verify", str(seal_file), "--key", key])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "VERIFIED ATTESTATION SEAL" in captured


def test_cli_attest_verify_has_no_default_key(tmp_path: Path, monkeypatch, capsys) -> None:
    """Without --key or AGENTWATCH_ATTEST_KEYS, verify refuses instead of using a public key."""
    for var in ("AGENTWATCH_ATTEST_KEYS", "AGENTWATCH_ATTEST_DEV_KEY"):
        monkeypatch.delenv(var, raising=False)
    seal = AttestationEngine.create_seal(merkle_root="m", tool_count=1, secret_key="k" * 40)
    seal_file = tmp_path / "seal.json"
    seal_file.write_text(seal.to_json(), encoding="utf-8")
    assert main(["attest", "verify", str(seal_file)]) == 2
    assert "no attestation signing key" in capsys.readouterr().err
    assert main(["attest", "verify", str(seal_file), "--key", "wrong" * 10]) == 1


def test_cli_replay(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """panopti replay replays recorded tape deterministically."""
    tape = ReplayTape()
    tape.record("step_a", {"x": 1}, "ok")
    tape_file = tmp_path / "tape.json"
    tape_file.write_text(tape.to_json(), encoding="utf-8")

    ret = main(["replay", str(tape_file)])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "REPLAY CONGRUENT" in captured


def test_cli_synth(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """panopti synth analyzes trace and outputs synthesized invariants."""
    events = [
        {"tool_name": "fetch"},
        {"tool_name": "fetch"},
        {"tool_name": "fetch"},
    ]
    trace_file = tmp_path / "trace.json"
    trace_file.write_text(json.dumps(events), encoding="utf-8")

    ret = main(["synth", str(trace_file)])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "SYNTHESIS COMPLETED" in captured
    assert "REPETITIVE_HAMMERING" in captured


def test_cli_help_prints_usage_without_running_anything(capsys: pytest.CaptureFixture[str]) -> None:
    """Bare `panopti` and `panopti --help` print usage and exit 0."""
    for argv in ([], ["--help"], ["-h"]):
        assert main(argv) == 0
        assert "Usage: panopti" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["demo", "--help"], "Usage: panopti demo"),
        (["playground", "--help"], "Usage: panopti playground"),
        (["doctor", "--help"], "Usage: panopti doctor"),
        (["top", "--help"], "Usage: panopti top|live"),
    ],
)
def test_cli_subcommand_help_exits_before_side_effects(
    argv: list[str], expected: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--help` after a subcommand must not start the interactive command."""
    assert main(argv) == 0
    assert expected in capsys.readouterr().out


def test_python_dash_m_dispatches_to_the_panopti_cli() -> None:
    """`python -m panopti` used to dispatch to the other CLI and lose every command."""
    proc = subprocess.run(
        [sys.executable, "-m", "panopti", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Usage: panopti" in proc.stdout


def test_ensure_utf8_stdio_survives_a_cp1252_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emoji output must not raise UnicodeEncodeError on a legacy Windows console."""
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("panoptis.cli._utils.sys.stdout", stream)

    ensure_utf8_stdio()

    assert stream.encoding.lower().replace("-", "") == "utf8"
    stream.write("🚫")  # would previously raise UnicodeEncodeError
    stream.detach()
