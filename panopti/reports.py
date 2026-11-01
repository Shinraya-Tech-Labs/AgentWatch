"""Human-readable reports for Panopti attestation seals.

Reads the plain dict form of a ``PanoptiAttestationSeal`` (``seal.to_dict()`` or the JSON file
written by ``seal.to_json()``) and renders it as Markdown or as one self-contained HTML page.

This module only presents a seal. It does not create seals and it does not check signatures;
use ``panopti attest verify`` for that. Standard library only.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

REPORT_FORMATS = ("md", "html")

# (key, label) in display order. Same fields as PanoptiAttestationSeal.
SEAL_FIELDS: tuple[tuple[str, str], ...] = (
    ("seal_id", "Seal ID"),
    ("policy_verdict", "Policy Verdict"),
    ("confidence_floor", "Confidence Floor"),
    ("loop_status", "Loop Status"),
    ("tool_count", "Tool Count"),
    ("timestamp_iso", "Timestamp (UTC)"),
    ("merkle_root", "Merkle Root"),
    ("signature", "Signature"),
    ("key_id", "Signing Key ID"),
    ("version", "Seal Version"),
)
# Fields version-1 seals do not have; shown as an em dash when absent.
OPTIONAL_FIELDS = frozenset({"key_id", "version"})

_BADGE_CLASS = {"COMPLIANT": "ok", "VIOLATION": "bad"}

_CSS = (
    "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:44rem;padding:0 1rem;"
    "color:#1f2328;background:#fff}"
    "h1{font-size:1.4rem}"
    ".badge{display:inline-block;padding:.3rem .9rem;border-radius:999px;color:#fff;"
    "font-weight:700;letter-spacing:.04em}"
    ".badge.ok{background:#1a7f37}.badge.bad{background:#cf222e}.badge.unknown{background:#6e7781}"
    "table{border-collapse:collapse;width:100%;margin-top:1.2rem}"
    "th,td{text-align:left;padding:.5rem .7rem;border-bottom:1px solid #d0d7de;"
    "vertical-align:top}"
    "th{width:11rem;color:#57606a;font-weight:600}"
    "td{font-family:ui-monospace,monospace;word-break:break-all}"
    "p.note{color:#57606a;font-size:.85rem;margin-top:1.5rem}"
)


class SealReportError(ValueError):
    """The seal data cannot be turned into a report."""


def _check(seal_data: Any) -> Mapping[str, Any]:
    if not isinstance(seal_data, Mapping):
        raise SealReportError("seal data must be a JSON object")
    missing = [key for key, _ in SEAL_FIELDS if key not in seal_data and key not in OPTIONAL_FIELDS]
    if missing:
        raise SealReportError("seal is missing required field(s): " + ", ".join(missing))
    return seal_data


def _text(key: str, value: Any) -> str:
    """Display text of one field. Numbers keep their natural form; the floor shows 4 digits."""
    if (
        key == "confidence_floor"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return f"{value:.4f}"
    return str(value)


def _verdict(seal: Mapping[str, Any]) -> tuple[str, str]:
    """Return (badge text, css class). Unknown verdicts get a neutral badge, not a green one."""
    verdict = str(seal["policy_verdict"])
    return verdict, _BADGE_CLASS.get(verdict.upper(), "unknown")


def _md_cell(text: str) -> str:
    """One Markdown table cell as a code span, so seal content is never read as Markdown/HTML."""
    one_line = " ".join(text.split()).replace("`", "'").replace("|", "\\|")
    return f"`{one_line}`"


def export_markdown_report(seal_data: Mapping[str, Any]) -> str:
    """Render a seal as a Markdown report. Raises SealReportError on a malformed seal."""
    seal = _check(seal_data)
    verdict, _ = _verdict(seal)
    icon = {"COMPLIANT": "[PASS]", "VIOLATION": "[FAIL]"}.get(verdict.upper(), "[????]")
    lines = [
        "# Panopti Attestation Seal Report",
        "",
        f"**Verdict: {icon} {_md_cell(verdict)}**",
        "",
        "| Field | Value |",
        "| --- | --- |",
    ]
    for key, label in SEAL_FIELDS:
        lines.append(f"| {label} | {_md_cell(_text(key, seal.get(key, '—')))} |")
    lines += [
        "",
        "_This report only presents the seal. It does not verify the signature; "
        "run `panopti attest verify` for that._",
        "",
    ]
    return "\n".join(lines)


def export_html_report(seal_data: Mapping[str, Any]) -> str:
    """Render a seal as one self-contained HTML page. Every value is HTML-escaped."""
    seal = _check(seal_data)
    verdict, css_class = _verdict(seal)
    rows = "\n".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(_text(key, seal.get(key, '—')))}</td></tr>"
        for key, label in SEAL_FIELDS
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Panopti Attestation Seal Report</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        "<h1>Panopti Attestation Seal Report</h1>\n"
        f'<span class="badge {css_class}">{html.escape(verdict)}</span>\n'
        f"<table>\n{rows}\n</table>\n"
        '<p class="note">This report only presents the seal. It does not verify the signature; '
        "run <code>panopti attest verify</code> for that.</p>\n"
        "</body>\n</html>\n"
    )


def export_report(seal_data: Mapping[str, Any], fmt: str = "md") -> str:
    """Render in ``fmt`` ("md" or "html")."""
    if fmt == "md":
        return export_markdown_report(seal_data)
    if fmt == "html":
        return export_html_report(seal_data)
    raise SealReportError(f"unknown format '{fmt}' (choose from: {', '.join(REPORT_FORMATS)})")
