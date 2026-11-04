"""Render the README's workflow diagrams and latency chart to docs/images/readme/.

    python docs/images/build_readme_images.py

Needs Playwright with Chromium. Set PANOPTIS_DIAGRAM_FONTS to a folder holding
inter-latin-400-normal.woff2 and inter-latin-600-normal.woff2 to use Inter; otherwise the
system sans-serif is used. The chart reads benchmarks/results/protect_eval/results.json, so
regenerate after re-running the evaluation.
"""

from __future__ import annotations

import base64
import json
import math
import os
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "images" / "readme"

INK, QUIET, EDGE = "#1f1e1b", "#5f5e58", "#c8c6bb"
ACCENT, ACCENT_TINT = "#2a6fdb", "#e8f0fc"
GOOD, WARN, BAD = "#2f8f5b", "#d9730d", "#d33a3a"
SECOND = "#d9730d"
MONO = "Consolas, 'Cascadia Mono', monospace"


def text(x, y, words, *, size=11.5, weight=400, fill=QUIET, anchor="start", family=None):
    font = f' font-family="{family}"' if family else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}"{font}>{escape(words)}</text>'
    )


def rect(x, y, w, h, *, stroke=EDGE, fill="none", width=1.25, dashed=False):
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{width}"{dash}/>'
    )


def box(x, y, w, h, name, lines=(), *, stroke=EDGE, fill="none", width=1.25, dashed=False):
    parts = [rect(x, y, w, h, stroke=stroke, fill=fill, width=width, dashed=dashed)]
    parts.append(text(x + 16, y + 24, name, size=13, weight=600, fill=INK))
    parts += [text(x + 16, y + 40 + 16 * i, line) for i, line in enumerate(lines)]
    return "".join(parts)


def accent(x, y, w, h, name, lines=()):
    return box(x, y, w, h, name, lines, stroke=ACCENT, fill=ACCENT_TINT, width=2)


def outcome(x, y, w, h, name, lines=(), colour=BAD):
    return box(x, y, w, h, name, lines, stroke=colour, width=1.5)


def container(x, y, w, h, name, *, tinted=False, dashed=False):
    stroke, fill, width = (ACCENT, "#f3f7fe", 2) if tinted else (EDGE, "none", 1.25)
    return rect(x, y, w, h, stroke=stroke, fill=fill, width=width, dashed=dashed) + text(
        x + 16, y + 24, name, size=13, weight=600, fill=INK
    )


def arrow(path, *, both=False):
    start = ' marker-start="url(#a)"' if both else ""
    return f'<path d="{path}" fill="none" stroke="{EDGE}" stroke-width="1.25"{start} marker-end="url(#a)"/>'


def line(path):
    return f'<path d="{path}" fill="none" stroke="{EDGE}" stroke-width="1.25"/>'


def svg(height, *parts):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 {height}" width="760" height="{height}">'
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{EDGE}"/></marker></defs>'
        + "".join(parts)
        + "</svg>"
    )


def hero():
    rows = [
        (56, "rm -rf /", "Blocked", "the tool never runs", BAD),
        (128, "git push --force origin release", "Asks a human", "runs once, after approval", WARN),
        (200, "ls -la", "Allowed", "the tool runs normally", GOOD),
        (
            272,
            "tool output: Ignore all previous…",
            "Sanitized",
            "line removed before the agent reads it",
            ACCENT,
        ),
    ]
    parts = [
        text(24, 36, "What the agent proposes", size=13, weight=600, fill=INK),
        text(476, 36, "What happens", size=13, weight=600, fill=INK),
        accent(324, 56, 112, 272, ""),
        text(380, 178, "PANOPTIS", size=14, weight=600, fill=INK, anchor="middle"),
        text(380, 198, "one decision", anchor="middle"),
        text(380, 214, "per tool call,", anchor="middle"),
        text(380, 230, "before it runs", anchor="middle"),
    ]
    for y, call, verdict, why, colour in rows:
        mid = y + 28
        parts += [
            rect(24, y, 260, 56),
            text(40, y + 33, call, size=12, fill=INK, family=MONO),
            arrow(f"M284 {mid}H322"),
            arrow(f"M436 {mid}H474"),
            outcome(476, y, 260, 56, verdict, [why], colour),
        ]
    return svg(352, *parts)


def how_it_works():
    return svg(
        416,
        box(
            24,
            24,
            712,
            56,
            "Agent",
            [
                "Claude Code, LangChain, LangGraph, AutoGen, smolagents, MCP clients or your own Python"
            ],
        ),
        container(24, 128, 712, 160, "", tinted=True),
        text(293, 152, "Enforcement boundary", size=13, weight=600, fill=INK, anchor="middle"),
        box(
            40,
            168,
            158,
            104,
            "1. Decide",
            ["identity, kill switch,", "threats, RBAC, loops,", "intent, approval"],
        ),
        box(
            214,
            168,
            158,
            104,
            "2. Run",
            ["only if allowed,", "at most once, with", "the decided arguments"],
        ),
        box(
            388,
            168,
            158,
            104,
            "3. Inspect",
            ["injection quarantined,", "secrets redacted,", "before the agent reads"],
        ),
        box(
            562,
            168,
            158,
            104,
            "4. Record",
            ["decision, execution", "and findings, when", "a ledger is set"],
        ),
        arrow("M198 220H212"),
        arrow("M372 220H386"),
        arrow("M546 220H560"),
        arrow("M119 80V166"),
        text(127, 112, "proposed tool call"),
        arrow("M467 166V82"),
        text(475, 112, "sanitized result"),
        outcome(40, 336, 158, 56, "Blocked", ["the tool never runs"]),
        box(
            214,
            336,
            332,
            56,
            "Tools and the world",
            ["files, shells, APIs, databases, MCP servers"],
        ),
        box(562, 336, 158, 56, "Evidence store", ["append-only, sealed"]),
        arrow("M119 272V334"),
        text(127, 310, "deny"),
        arrow("M293 272V334", both=True),
        arrow("M641 272V334"),
    )


def architecture():
    return svg(
        544,
        container(24, 24, 712, 120, "Enforcing integrations"),
        box(40, 56, 158, 72, "Python SDK", ["protect_tool()", "watch(protection=)"]),
        box(214, 56, 158, 72, "Frameworks", ["LangChain, LangGraph,", "AutoGen, smolagents"]),
        box(388, 56, 158, 72, "Claude Code", ["PreToolUse and", "PostToolUse hooks"]),
        box(562, 56, 158, 72, "MCP gateway", ["stdio and", "Streamable HTTP"]),
        arrow("M188 144V198"),
        text(196, 176, "every enforced tool call"),
        container(24, 200, 328, 192, "Enforcement plane: panoptis.protect", tinted=True),
        box(40, 232, 140, 64, "Pipeline", ["every tool call"]),
        box(196, 232, 140, 64, "Approvals", ["digest-bound"]),
        box(40, 312, 140, 64, "Emergency stop", ["and suspensions"]),
        box(196, 312, 140, 64, "Output checks", ["before the agent"]),
        container(408, 200, 328, 192, "Evidence plane: panoptis v3"),
        box(424, 232, 140, 64, "Evidence store", ["append-only"]),
        box(580, 232, 140, 64, "Merkle seals", ["hash-chained"]),
        box(424, 312, 140, 64, "Replay and diff", ["from evidence"]),
        box(580, 312, 140, 64, "Verifier", ["standard library"]),
        arrow("M352 296H406"),
        text(380, 286, "records", anchor="middle"),
        arrow("M188 392V446"),
        arrow("M572 392V446"),
        container(24, 448, 712, 72, ""),
        text(40, 472, "Shared services", size=13, weight=600, fill=INK),
        text(
            40,
            496,
            "REST API and WebSocket events · security dashboard · panoptis CLI · SQLite or PostgreSQL storage",
            size=12,
        ),
    )


def pipeline():
    steps = [
        ("1. Identity", "agent, session and tenant"),
        ("2. Control", "kill switch and suspensions"),
        ("3. Threat analysis", "shell, SQL, paths, SSRF, Python, secrets"),
        ("4. RBAC", "tools, arguments and rate limits"),
        ("5. Loops", "repeats, thrashing, circuit breaker"),
        ("6. Intent", "the action against the stated objective"),
        ("7. Human approval, when escalated", "exact action digest; timeout denies"),
        ("8. Final stop check", "the kill switch again, just before running"),
        ("9. Run", "the tool executes at most once"),
        ("10. Inspect output", "quarantine injection, redact secrets"),
    ]
    parts = []
    for i, (name, line_) in enumerate(steps):
        y = 24 + i * 64
        parts.append(box(40, y, 300, 48, name, []))
        parts[-1] = (
            rect(40, y, 300, 48)
            + text(56, y + 20, name, size=13, weight=600, fill=INK)
            + text(56, y + 37, line_)
        )
        if i:
            parts.append(arrow(f"M190 {y - 16}V{y - 2}"))
    last = 24 + len(steps) * 64
    parts += [
        arrow(f"M190 {last - 16}V{last - 2}"),
        rect(40, last, 680, 48, stroke=ACCENT, fill=ACCENT_TINT, width=2),
        text(
            380,
            last + 29,
            "Result reaches the agent; with a ledger set, every step is recorded",
            size=13,
            weight=600,
            fill=INK,
            anchor="middle",
        ),
        # stages 1 to 6 share one exit
        line("M340 48H356M340 112H356M340 176H356M340 240H356M340 304H356M340 368H356M356 48V368"),
        arrow("M356 216H438"),
        text(397, 208, "block", anchor="middle"),
        outcome(440, 192, 280, 48, "Refused", ["the tool body never runs"]),
        arrow("M340 432H580V242"),
        text(397, 424, "denied or timed out", anchor="middle"),
        arrow("M340 496H660V242"),
        text(397, 488, "stop tripped", anchor="middle"),
        arrow("M340 624H438"),
        text(389, 616, "unsafe result", anchor="middle"),
        outcome(440, 600, 280, 48, "Result withheld or sanitized", ["recorded as executed"], WARN),
        container(440, 24, 280, 136, "Opt-in stages", dashed=True),
        text(456, 68, "information-flow lattice, trajectory"),
        text(456, 84, "checks, steganography cleaning,"),
        text(456, 100, "Crescendo drift, memory sentry,"),
        text(456, 116, "RAG sanitizer. Off by default;"),
        text(456, 132, "ProtectionConfig.sovereign() enables them"),
    ]
    return svg(last + 72, *parts)


def evidence():
    return svg(
        452,
        box(
            24,
            24,
            712,
            56,
            "Records",
            [
                "decisions, approvals, executions, output findings, control actions, discovery inventories"
            ],
        ),
        arrow("M380 80V126"),
        text(388, 108, "sealed every 200 records or 30 seconds, per tenant"),
        container(24, 128, 712, 120, "Hash-chained Merkle segments"),
        box(
            40,
            160,
            200,
            72,
            "Segment n−1",
            ["Merkle root of its leaves", "hash of the segment before"],
        ),
        box(280, 160, 200, 72, "Segment n", ["Merkle root of its leaves", "hash of segment n−1"]),
        accent(
            520, 160, 200, 72, "Head", ["latest sequence and hash", "anchored outside the store"]
        ),
        arrow("M240 196H278"),
        arrow("M480 196H518"),
        arrow("M188 248V294"),
        text(196, 276, "exported bundle"),
        arrow("M572 248V294"),
        text(580, 276, "expected head"),
        accent(
            24,
            296,
            712,
            56,
            "Standalone verifier",
            [
                "Python standard library only: checks leaves, Merkle roots, the chain, the anchor and inclusion proofs"
            ],
        ),
        arrow("M188 352V394"),
        arrow("M572 352V394"),
        outcome(24, 396, 332, 40, "Intact", [], GOOD),
        outcome(404, 396, 332, 40, "Edited, reordered or truncated: fails", [], BAD),
    )


def replay():
    return svg(
        352,
        box(
            24,
            24,
            220,
            72,
            "Shadow replay runner",
            ["replays each recorded step", "against the model under test"],
        ),
        box(288, 24, 192, 72, "Model under test", ["proposes its next", "tool action"]),
        box(
            524,
            24,
            212,
            72,
            "Recorded evidence",
            ["serves the recorded result", "for that tool call"],
        ),
        arrow("M244 48H286"),
        arrow("M286 72H246"),
        arrow("M384 96V128H630V98", both=True),
        text(507, 120, "lookup, no real execution", anchor="middle"),
        rect(524, 136, 212, 40, dashed=True),
        text(630, 161, "Real world: never contacted", anchor="middle"),
        arrow("M134 96V206"),
        text(142, 156, "compare the traces"),
        accent(24, 208, 712, 40, "Compared on tool, arguments, results, status and planner text"),
        line("M380 248V272M107 272H653"),
        arrow("M107 272V286"),
        arrow("M289 272V286"),
        arrow("M471 272V286"),
        arrow("M653 272V286"),
        box(24, 288, 166, 40, "Identical replay", []),
        box(206, 288, 166, 40, "Failure reproduced", []),
        box(388, 288, 166, 40, "Model diverged", []),
        box(570, 288, 166, 40, "Not enough evidence", []),
    )


def contributing():
    steps = [
        (24, "Discuss", ["on Discord or", "in an issue"]),
        (172, "Get assigned", ["required before", "work begins"]),
        (320, "Build and test", ["small diff,", "tests included"]),
        (468, "Open a PR", ["CI checks", "every change"]),
    ]
    parts = [box(x, 24, 124, 72, name, lines) for x, name, lines in steps]
    parts += [arrow(f"M{x + 124} 60H{x + 146}") for x, *_ in steps]
    parts.append(accent(616, 24, 120, 72, "Merged", ["name on the", "contributors wall"]))
    return svg(120, *parts)


def latency():
    data = json.loads(
        (ROOT / "benchmarks/results/protect_eval/results.json").read_text(encoding="utf-8")
    )["overhead"]
    rows = [
        ("Allow", "allow"),
        ("Block", "block"),
        ("Rewrite arguments", "mutate"),
        ("Sanitize output", "output_sanitize"),
        ("Approved escalation", "escalate_auto_approved"),
    ]
    x0, x1, lo, hi = 190, 700, 0.1, 100.0

    def px(ms):
        return x0 + (math.log10(ms) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * (
            x1 - x0
        )

    top, step = 72, 64
    bottom = top + step * len(rows)
    parts = [
        f'<circle cx="32" cy="30" r="5" fill="{ACCENT}"/>',
        text(44, 34, "No evidence recording", size=12, fill=INK),
        f'<circle cx="208" cy="30" r="5" fill="{SECOND}"/>',
        text(220, 34, "SQLite evidence store", size=12, fill=INK),
        f'<path d="M400 30H432" stroke="{QUIET}" stroke-width="2" stroke-linecap="round"/>',
        f'<circle cx="400" cy="30" r="4" fill="{QUIET}"/>',
        text(442, 34, "dot is p50, line runs to p99", size=12, fill=INK),
    ]
    for tick in (0.1, 1, 10, 100):
        x = px(tick)
        parts.append(f'<path d="M{x:.1f} {top - 8}V{bottom}" stroke="#ecebe4" stroke-width="1"/>')
        parts.append(text(f"{x:.1f}", bottom + 20, f"{tick:g} ms", anchor="middle"))
    for i, (label, key) in enumerate(rows):
        y = top + i * step + 18
        parts.append(text(24, y + 13, label, size=12.5, fill=INK))
        for lane, suffix, colour in ((0, "no_ledger", ACCENT), (1, "sqlite_ledger", SECOND)):
            row = data[f"{key}/{suffix}"]
            yy = y + lane * 22
            a, b = px(row["p50_ms"]), px(row["p99_ms"])
            parts.append(
                f'<path d="M{a:.1f} {yy}H{b:.1f}" stroke="{colour}" stroke-width="2" stroke-linecap="round"/>'
            )
            parts.append(
                f'<circle cx="{a:.1f}" cy="{yy}" r="5.5" fill="{colour}" stroke="white" stroke-width="2"/>'
            )
            parts.append(
                text(
                    f"{a - 10:.1f}", yy + 4, f"{row['p50_ms']:.2f}", size=11, fill=INK, anchor="end"
                )
            )
    return svg(bottom + 40, *parts)


IMAGES = {
    "hero": hero,
    "how-it-works": how_it_works,
    "architecture": architecture,
    "decision-pipeline": pipeline,
    "evidence": evidence,
    "shadow-replay": replay,
    "contributing": contributing,
    "benchmark-latency": latency,
}


def font_css() -> str:
    folder = os.environ.get("PANOPTIS_DIAGRAM_FONTS")
    if not folder:
        return ""
    faces = []
    for weight in (400, 600):
        raw = (Path(folder) / f"inter-latin-{weight}-normal.woff2").read_bytes()
        data = base64.b64encode(raw).decode()
        faces.append(
            f"@font-face{{font-family:Inter;font-weight:{weight};src:url(data:font/woff2;base64,{data})}}"
        )
    return "".join(faces)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    style = (
        font_css()
        + "html,body{margin:0;background:#fff}svg{display:block;font-family:Inter,'Segoe UI',system-ui,sans-serif}"
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 760, "height": 600}, device_scale_factor=2)
        for name, draw in IMAGES.items():
            page.set_content(f"<!doctype html><style>{style}</style>{draw()}")
            page.evaluate("document.fonts.ready")
            page.locator("svg").screenshot(path=str(OUT / f"{name}.png"))
            print(name)
        browser.close()


if __name__ == "__main__":
    main()
