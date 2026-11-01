"""Tests for panopti.reports and the ``panopti attest report`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from panopti.attest import AttestationEngine
from panopti.cli import main
from panopti.reports import (
    SEAL_FIELDS,
    SealReportError,
    export_html_report,
    export_markdown_report,
    export_report,
)

HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>'
TEST_KEY = "report-tests-" + "k" * 32  # these tests are about rendering, not key management


@pytest.fixture(autouse=True)
def _attest_key(monkeypatch):
    import base64

    monkeypatch.setenv(
        "AGENTWATCH_ATTEST_KEYS", "reports=" + base64.b64encode(TEST_KEY.encode()).decode()
    )


def _seal(**kw: object) -> dict:
    kw.setdefault("merkle_root", "abc123merkle")
    kw.setdefault("tool_count", 3)
    kw.setdefault("confidence_floor", 0.95)
    return AttestationEngine.create_seal(**kw).to_dict()  # type: ignore[arg-type]


def _write(tmp_path: Path, data: dict, name: str = "seal.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# --- report functions ---------------------------------------------------------------------


def test_markdown_contains_every_field_and_compliant_verdict() -> None:
    seal = _seal()
    md = export_markdown_report(seal)
    for key, label in SEAL_FIELDS:
        assert label in md
        assert str(seal[key]) in md or f"{seal[key]:.4f}" in md
    assert "[PASS]" in md and "COMPLIANT" in md
    assert "0.9500" in md


def test_markdown_violation_verdict() -> None:
    md = export_markdown_report(_seal(is_compliant=False, loop_detected=True))
    assert "[FAIL]" in md and "VIOLATION" in md
    assert "LOOP_DETECTED" in md


def test_html_compliant_badge_is_green() -> None:
    page = export_html_report(_seal())
    assert 'class="badge ok">COMPLIANT<' in page
    assert "badge bad" not in page.split("</style>")[1]
    assert page.startswith("<!DOCTYPE html>")


def test_html_violation_badge_is_red() -> None:
    page = export_html_report(_seal(is_compliant=False))
    assert 'class="badge bad">VIOLATION<' in page


def test_html_is_self_contained() -> None:
    page = export_html_report(_seal())
    assert "<style>" in page
    for external in ("<script", "<link", "src=", "http://", "https://"):
        assert external not in page


def test_html_escapes_hostile_values() -> None:
    seal = _seal()
    for key in ("seal_id", "merkle_root", "loop_status", "timestamp_iso", "signature"):
        seal[key] = HOSTILE
    seal["policy_verdict"] = HOSTILE
    page = export_html_report(seal)
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;" in page
    # a hostile verdict is shown, but never gets the green badge
    assert 'class="badge unknown"' in page


def test_markdown_keeps_hostile_values_inside_code_spans() -> None:
    seal = _seal()
    seal["seal_id"] = "a|b`c\nd"
    md = export_markdown_report(seal)
    assert "| Seal ID | `a\\|b'c d` |" in md
    seal["merkle_root"] = HOSTILE
    assert f"`{HOSTILE}`" in export_markdown_report(seal)


@pytest.mark.parametrize("render", [export_markdown_report, export_html_report])
def test_missing_key_raises_clear_error(render) -> None:
    seal = _seal()
    del seal["merkle_root"]
    del seal["signature"]
    with pytest.raises(SealReportError, match="merkle_root, signature"):
        render(seal)


@pytest.mark.parametrize("render", [export_markdown_report, export_html_report])
def test_non_object_seal_raises(render) -> None:
    with pytest.raises(SealReportError, match="JSON object"):
        render(["not", "a", "seal"])


def test_export_report_rejects_unknown_format() -> None:
    with pytest.raises(SealReportError, match="unknown format 'pdf'"):
        export_report(_seal(), "pdf")


def test_input_seal_is_not_modified() -> None:
    seal = _seal()
    before = dict(seal)
    export_markdown_report(seal)
    export_html_report(seal)
    assert seal == before


# --- CLI ----------------------------------------------------------------------------------


def test_cli_default_prints_markdown_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seal_file = _write(tmp_path, _seal())
    assert main(["attest", "report", str(seal_file)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Panopti Attestation Seal Report")


def test_cli_html_to_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seal_file = _write(tmp_path, _seal())
    assert main(["attest", "report", str(seal_file), "--format", "html"]) == 0
    assert capsys.readouterr().out.startswith("<!DOCTYPE html>")


@pytest.mark.parametrize(
    ("fmt", "needle"), [("md", "# Panopti Attestation Seal Report"), ("html", "<!DOCTYPE html>")]
)
def test_cli_output_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fmt: str, needle: str
) -> None:
    seal_file = _write(tmp_path, _seal(is_compliant=False))
    out_file = tmp_path / f"report.{fmt}"
    assert main(["attest", "report", str(seal_file), "-f", fmt, "-o", str(out_file)]) == 0
    assert needle in out_file.read_text(encoding="utf-8")
    assert "VIOLATION" in out_file.read_text(encoding="utf-8")
    assert str(out_file) in capsys.readouterr().out


def test_cli_bad_format_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seal_file = _write(tmp_path, _seal())
    assert main(["attest", "report", str(seal_file), "--format", "pdf"]) == 1
    assert "unknown format 'pdf'" in capsys.readouterr().err


def test_cli_missing_file_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["attest", "report", str(tmp_path / "nope.json")]) == 1
    assert "Error reading seal file" in capsys.readouterr().err


def test_cli_invalid_json_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["attest", "report", str(bad)]) == 1
    assert "Error reading seal file" in capsys.readouterr().err


def test_cli_malformed_seal_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seal = _seal()
    del seal["policy_verdict"]
    assert main(["attest", "report", str(_write(tmp_path, seal))]) == 1
    assert "policy_verdict" in capsys.readouterr().err


def test_cli_unwritable_output_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seal_file = _write(tmp_path, _seal())
    target = tmp_path / "missing_dir" / "r.md"
    assert main(["attest", "report", str(seal_file), "--output", str(target)]) == 1
    assert "Error writing report" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra",
    [[], ["--format"], ["--output"], ["--bogus"], ["a.json", "b.json"]],
)
def test_cli_usage_errors_return_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    argv = ["attest", "report", *extra]
    assert main(argv) == 1
    assert "panopti attest report" in capsys.readouterr().err


def test_cli_verify_still_works(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seal = AttestationEngine.create_seal(merkle_root="m", tool_count=1)
    seal_file = tmp_path / "s.json"
    seal_file.write_text(seal.to_json(), encoding="utf-8")
    assert main(["attest", "verify", str(seal_file)]) == 0
    assert "VERIFIED ATTESTATION SEAL" in capsys.readouterr().out


def test_report_shows_the_signing_key_and_accepts_legacy_seals():
    seal = _seal()
    md = export_markdown_report(seal)
    assert (
        "| Signing Key ID | `reports` |" in md
        or "reports" in md.split("Signing Key ID")[1].split("\n")[0]
    )
    legacy = {k: v for k, v in seal.items() if k not in ("key_id", "version")}
    assert "Signing Key ID" in export_html_report(legacy)
