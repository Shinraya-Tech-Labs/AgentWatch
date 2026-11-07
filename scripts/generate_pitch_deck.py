#!/usr/bin/env python3
"""Script to generate the winning Unstop Pitch Deck for Panoptis / AgentWatch.
Adheres strictly to the 5 evaluation pillars (Tech Architecture, Idea & Originality,
Impact & Scale, Live Demo & Pitch, UX Craft & Polish).
"""

from __future__ import annotations

import os
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt


def create_deck(filename: str = "PANOPTIS_UNSTOP_PITCH_DECK.pptx") -> str:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]

    # Color Palette: Cyber Slate & Electric Emerald / Cyan
    c_bg_dark = RGBColor(15, 23, 42)       # Slate 900
    c_card_dark = RGBColor(30, 41, 59)     # Slate 800
    c_card_border = RGBColor(51, 65, 85)   # Slate 700
    c_teal = RGBColor(13, 148, 136)        # Teal 600
    c_cyan = RGBColor(56, 189, 248)        # Cyan 400
    c_emerald = RGBColor(16, 185, 129)     # Emerald 500
    c_red = RGBColor(239, 68, 68)          # Red 500
    c_amber = RGBColor(245, 158, 11)       # Amber 500
    c_white = RGBColor(255, 255, 255)
    c_gray_light = RGBColor(226, 232, 240) # Slate 200
    c_gray_muted = RGBColor(148, 163, 184) # Slate 400

    def set_slide_background(slide, color=c_bg_dark):
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = color

    def add_header(slide, pillar_tag: str, title: str, subtitle: str = ""):
        # Category Tag
        tag_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.35))
        tf_tag = tag_box.text_frame
        tf_tag.word_wrap = True
        p_tag = tf_tag.paragraphs[0]
        p_tag.text = pillar_tag.upper()
        p_tag.font.name = "Arial"
        p_tag.font.size = Pt(10)
        p_tag.font.bold = True
        p_tag.font.color.rgb = c_teal

        # Title
        title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.7), Inches(11.7), Inches(0.6))
        tf_title = title_box.text_frame
        tf_title.word_wrap = True
        p_title = tf_title.paragraphs[0]
        p_title.text = title
        p_title.font.name = "Arial"
        p_title.font.size = Pt(22)
        p_title.font.bold = True
        p_title.font.color.rgb = c_white

        # Subtitle
        if subtitle:
            sub_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.25), Inches(11.7), Inches(0.35))
            tf_sub = sub_box.text_frame
            tf_sub.word_wrap = True
            p_sub = tf_sub.paragraphs[0]
            p_sub.text = subtitle
            p_sub.font.name = "Arial"
            p_sub.font.size = Pt(11)
            p_sub.font.color.rgb = c_gray_muted

    def add_card(slide, left, top, width, height, title="", border_color=c_card_border, bg_color=c_card_dark):
        shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        shape.line.color.rgb = border_color
        shape.line.width = Pt(1.5)

        if title:
            tb = slide.shapes.add_textbox(left + Inches(0.2), top + Inches(0.15), width - Inches(0.4), Inches(0.4))
            p = tb.text_frame.paragraphs[0]
            p.text = title
            p.font.name = "Arial"
            p.font.size = Pt(12)
            p.font.bold = True
            p.font.color.rgb = c_cyan

        return shape

    # =========================================================================
    # SLIDE 1: TITLE SLIDE (HOOK & PROMISE)
    # =========================================================================
    slide1 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide1)

    # Accent Top Line
    top_line = slide1.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(0.8), Inches(1.5), Inches(0.06))
    top_line.fill.solid()
    top_line.fill.fore_color.rgb = c_teal
    top_line.line.fill.background()

    tb1 = slide1.shapes.add_textbox(Inches(0.8), Inches(1.1), Inches(11.7), Inches(3.2))
    tf1 = tb1.text_frame
    tf1.word_wrap = True

    p_badge = tf1.paragraphs[0]
    p_badge.text = "IDEA SUBMISSION · UNSTOP PPT ROUND · 2026 FRONTIER AI SAFETY"
    p_badge.font.name = "Arial"
    p_badge.font.size = Pt(11)
    p_badge.font.bold = True
    p_badge.font.color.rgb = c_teal

    p_main = tf1.add_paragraph()
    p_main.text = "PANOPTIS (AgentWatch)"
    p_main.font.name = "Arial"
    p_main.font.size = Pt(38)
    p_main.font.bold = True
    p_main.font.color.rgb = c_white
    p_main.space_before = Pt(8)

    p_sub = tf1.add_paragraph()
    p_sub.text = "The Sovereign Runtime Hypervisor for Autonomous Multi-Agent Swarms"
    p_sub.font.name = "Arial"
    p_sub.font.size = Pt(18)
    p_sub.font.color.rgb = c_cyan
    p_sub.space_before = Pt(6)

    p_desc = tf1.add_paragraph()
    p_desc.text = (
        "Deterministic Pre-Execution Tool Mediation, Dual-Lattice Information Flow Control (IFC), "
        "and Cryptographic Merkle Inclusion Proofs for Google ADK, Gemini 2.0 & Production Agent Runtimes."
    )
    p_desc.font.name = "Arial"
    p_desc.font.size = Pt(12)
    p_desc.font.color.rgb = c_gray_light
    p_desc.space_before = Pt(12)

    # 3 Metrics Callouts at bottom
    add_card(slide1, Inches(0.8), Inches(4.8), Inches(3.6), Inches(1.8), border_color=c_emerald)
    tb_m1 = slide1.shapes.add_textbox(Inches(1.0), Inches(5.0), Inches(3.2), Inches(1.4))
    p_num1 = tb_m1.text_frame.paragraphs[0]
    p_num1.text = "0.3 ms"
    p_num1.font.size = Pt(28)
    p_num1.font.bold = True
    p_num1.font.color.rgb = c_emerald
    p_lbl1 = tb_m1.text_frame.add_paragraph()
    p_lbl1.text = "Inline Latency Overhead (p50)\nZero perceptible lag on Gemini loops."
    p_lbl1.font.size = Pt(10)
    p_lbl1.font.color.rgb = c_gray_light

    add_card(slide1, Inches(4.866), Inches(4.8), Inches(3.6), Inches(1.8), border_color=c_cyan)
    tb_m2 = slide1.shapes.add_textbox(Inches(5.066), Inches(5.0), Inches(3.2), Inches(1.4))
    p_num2 = tb_m2.text_frame.paragraphs[0]
    p_num2.text = "100% Fail-Closed"
    p_num2.font.size = Pt(24)
    p_num2.font.bold = True
    p_num2.font.color.rgb = c_cyan
    p_lbl2 = tb_m2.text_frame.add_paragraph()
    p_lbl2.text = "Pre-Execution Syscall Veto\nBlocks attacks before code executes."
    p_lbl2.font.size = Pt(10)
    p_lbl2.font.color.rgb = c_gray_light

    add_card(slide1, Inches(8.933), Inches(4.8), Inches(3.6), Inches(1.8), border_color=c_teal)
    tb_m3 = slide1.shapes.add_textbox(Inches(9.133), Inches(5.0), Inches(3.2), Inches(1.4))
    p_num3 = tb_m3.text_frame.paragraphs[0]
    p_num3.text = "SHA-256 Merkle"
    p_num3.font.size = Pt(24)
    p_num3.font.bold = True
    p_num3.font.color.rgb = c_teal
    p_lbl3 = tb_m3.text_frame.add_paragraph()
    p_lbl3.text = "Witness Court & Audit Seals\nEU AI Act & SOC2 Type II audit-ready."
    p_lbl3.font.size = Pt(10)
    p_lbl3.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 2: PILLAR 02 · IDEA & ORIGINALITY — THE PROBLEM STATEMENT
    # =========================================================================
    slide2 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide2)
    add_header(
        slide2,
        "Pillar 02 · Idea & Originality (20% Weight)",
        "The Problem Statement: The Agentic Security Crisis & The Lethal Trifecta",
        "The Single Non-Obvious Insight: An autonomous agent cannot evaluate its own safety."
    )

    # Left Card: The Paradigm Shift
    add_card(slide2, Inches(0.8), Inches(1.8), Inches(5.6), Inches(5.0), "The 2026 Agentic Paradox", border_color=c_red)
    tb_c1 = slide2.shapes.add_textbox(Inches(1.0), Inches(2.4), Inches(5.2), Inches(4.2))
    tf_c1 = tb_c1.text_frame
    tf_c1.word_wrap = True
    points1 = [
        ("From Chatbots to Action Engines: ", "In 2023, LLMs output text strings (risk = hallucination). In 2026, autonomous agents possess shell access, production database drivers, cloud credentials, and web browsing capabilities."),
        ("The Self-Moderation Fallacy: ", "Asking an LLM 'Are you executing a safe command?' fails under prompt injection. When external context rewrites model reasoning, the agent becomes an authenticated insider threat."),
        ("The Lethal Trifecta: ", "When an agent simultaneously gains (1) Untrusted Input Reading, (2) Internal State/Secrets Access, and (3) Outbound Sinks, exfiltration is mathematically guaranteed."),
    ]
    for bold_txt, norm_txt in points1:
        p = tf_c1.add_paragraph() if tf_c1.paragraphs[0].text else tf_c1.paragraphs[0]
        p.space_after = Pt(10)
        run_b = p.add_run()
        run_b.text = "• " + bold_txt
        run_b.font.bold = True
        run_b.font.size = Pt(11)
        run_b.font.color.rgb = c_white
        run_n = p.add_run()
        run_n.text = norm_txt
        run_n.font.size = Pt(10.5)
        run_n.font.color.rgb = c_gray_light

    # Right Card: The Execution Blind Spot
    add_card(slide2, Inches(6.9), Inches(1.8), Inches(5.6), Inches(5.0), "Why Existing Defenses Completely Fail", border_color=c_amber)
    tb_c2 = slide2.shapes.add_textbox(Inches(7.1), Inches(2.4), Inches(5.2), Inches(4.2))
    tf_c2 = tb_c2.text_frame
    tf_c2.word_wrap = True
    points2 = [
        ("Prompt Firewalls (Model Armor / NeMo): ", "Operate strictly on natural language tokens at the front door. Completely blind to multi-turn piecewise droppers, encoded Base64 arguments, and indirect injections in files."),
        ("Container Isolation (gVisor / Docker): ", "Isolates the host OS from container escapes, but treats the agent as root inside the container. Inside the box, the rogue agent has full freedom to drop production databases or curl credentials outbound."),
        ("The Execution Boundary Blind Spot: ", "Neither prompt filters nor container kernels observe tool argument ASTs before execution. That is where catastrophic damage occurs."),
    ]
    for bold_txt, norm_txt in points2:
        p = tf_c2.add_paragraph() if tf_c2.paragraphs[0].text else tf_c2.paragraphs[0]
        p.space_after = Pt(10)
        run_b = p.add_run()
        run_b.text = "• " + bold_txt
        run_b.font.bold = True
        run_b.font.size = Pt(11)
        run_b.font.color.rgb = c_white
        run_n = p.add_run()
        run_n.text = norm_txt
        run_n.font.size = Pt(10.5)
        run_n.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 3: PROPOSED SOLUTION & ARCHITECTURE
    # =========================================================================
    slide3 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide3)
    add_header(
        slide3,
        "Pillar 01 · Tech Architecture (25% Weight)",
        "The Proposed Solution: Panoptis Runtime Execution Hypervisor",
        "Deterministic, Microsecond-Latency Mediation Layer Operating Below the Reasoning Core"
    )

    # 3 Horizontal Process Cards
    card_w = Inches(3.64)
    h_top = Inches(1.8)
    h_len = Inches(5.0)

    # Step 1
    add_card(slide3, Inches(0.8), h_top, card_w, h_len, "1. Intercept & Evaluate", border_color=c_cyan)
    tb_s1 = slide3.shapes.add_textbox(Inches(1.0), h_top + Inches(0.6), card_w - Inches(0.4), h_len - Inches(0.8))
    tf_s1 = tb_s1.text_frame
    tf_s1.word_wrap = True
    s1_text = [
        ("Pre-Execution Interception: ", "Catches tool calls (Bash, SQL, API) via Google ADK before_tool_callback or Claude Code PreToolUse hook."),
        ("0.3 ms Inline Pipeline: ", "Evaluates RBAC permissions, destructive command traps, and kill-switch state within 300 microseconds."),
        ("Fail-Closed Guarantees: ", "Any engine crash, syntax error, or unhandled exception defaults to BLOCK (Verdict.BLOCK)."),
    ]
    for b, n in s1_text:
        p = tf_s1.add_paragraph() if tf_s1.paragraphs[0].text else tf_s1.paragraphs[0]
        p.space_after = Pt(8)
        rb = p.add_run()
        rb.text = b
        rb.font.bold = True
        rb.font.size = Pt(10)
        rb.font.color.rgb = c_cyan
        rn = p.add_run()
        rn.text = n
        rn.font.size = Pt(9.5)
        rn.font.color.rgb = c_gray_light

    # Step 2
    add_card(slide3, Inches(4.84), h_top, card_w, h_len, "2. Mediate & Quarantine", border_color=c_teal)
    tb_s2 = slide3.shapes.add_textbox(Inches(5.04), h_top + Inches(0.6), card_w - Inches(0.4), h_len - Inches(0.8))
    tf_s2 = tb_s2.text_frame
    tf_s2.word_wrap = True
    s2_text = [
        ("Tri-State Verdicts: ", "Produces ALLOW (nominal), BLOCK (destructive attack), or ESCALATE_HITL (sensitive boundary)."),
        ("Dual-Lattice IFC: ", "Mathematical tracking of Confidentiality (L_C) & Integrity (L_I); forbids untrusted taints flowing to network sinks."),
        ("Circuit Breaker FSM: ", "In-memory FSM trips to OPEN on consecutive errors or parameter oscillations, halting runaway agent loops."),
    ]
    for b, n in s2_text:
        p = tf_s2.add_paragraph() if tf_s2.paragraphs[0].text else tf_s2.paragraphs[0]
        p.space_after = Pt(8)
        rb = p.add_run()
        rb.text = b
        rb.font.bold = True
        rb.font.size = Pt(10)
        rb.font.color.rgb = c_teal
        rn = p.add_run()
        rn.text = n
        rn.font.size = Pt(9.5)
        rn.font.color.rgb = c_gray_light

    # Step 3
    add_card(slide3, Inches(8.88), h_top, card_w, h_len, "3. Attest & Seal", border_color=c_emerald)
    tb_s3 = slide3.shapes.add_textbox(Inches(9.08), h_top + Inches(0.6), card_w - Inches(0.4), h_len - Inches(0.8))
    tf_s3 = tb_s3.text_frame
    tf_s3.word_wrap = True
    s3_text = [
        ("Merkle Witness Court: ", "Serializes tool actions and verdicts into RFC 8785 canonical JSON; commits leaves into Merkle trees."),
        ("Inclusion Proofs: ", "Exports standalone cryptographic inclusion proofs for regulatory auditors (EU AI Act Art 12 / SOC2)."),
        ("CoW Rollback Engine: ", "Copy-on-Write workspace snapshots rollback unauthorized disk changes in milliseconds."),
    ]
    for b, n in s3_text:
        p = tf_s3.add_paragraph() if tf_s3.paragraphs[0].text else tf_s3.paragraphs[0]
        p.space_after = Pt(8)
        rb = p.add_run()
        rb.text = b
        rb.font.bold = True
        rb.font.size = Pt(10)
        rb.font.color.rgb = c_emerald
        rn = p.add_run()
        rn.text = n
        rn.font.size = Pt(9.5)
        rn.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 4: PILLAR 01 · ALGORITHMIC DEPTH & INNOVATION
    # =========================================================================
    slide4 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide4)
    add_header(
        slide4,
        "Pillar 01 & 02 · Technical Depth & Algorithmic Complexity",
        "Algorithmic Depth: Mathematical Logics & Sentry Subsystems",
        "No simple API gluing — Custom Logics, Sequential Drift Statistics, and Dynamic Lattices."
    )

    # 4 Quadrant Grid
    gw = Inches(5.6)
    gh = Inches(2.4)

    # Top-Left: Dual-Lattice IFC
    add_card(slide4, Inches(0.8), Inches(1.8), gw, gh, "A. Dual-Lattice Information Flow Control (IFC)", border_color=c_teal)
    tb_q1 = slide4.shapes.add_textbox(Inches(1.0), Inches(2.3), gw - Inches(0.4), gh - Inches(0.6))
    tf_q1 = tb_q1.text_frame
    tf_q1.word_wrap = True
    p1 = tf_q1.paragraphs[0]
    p1.text = "• Formal Theorem: PermissibleFlow(S, K) <=> (C(S) <= C(K)) & (I(S) >= I(K))\n" \
              "• 8-Codec Pipeline: Base64, Hex, URL-encoding, Unicode homoglyphs, and 4-gram Jaccard Shingling to stop secret obfuscation."
    p1.font.size = Pt(9.5)
    p1.font.color.rgb = c_gray_light

    # Top-Right: Metric Temporal Logic
    add_card(slide4, Inches(6.9), Inches(1.8), gw, gh, "B. Metric Temporal Logic (MTL) Trajectory Engine", border_color=c_cyan)
    tb_q2 = slide4.shapes.add_textbox(Inches(7.1), Inches(2.3), gw - Inches(0.4), gh - Inches(0.6))
    tf_q2 = tb_q2.text_frame
    tf_q2.word_wrap = True
    p2 = tf_q2.paragraphs[0]
    p2.text = "• Cumulative AST Tracking: phi = Always(untrusted_read -> Not Eventually[0, tau] network_sink)\n" \
              "• Piecewise Dropper Defense: Detects Turn 1 staged decoders, Turn 2 chunks, and Turn 3 execution attempts across bounded LRU session states."
    p2.font.size = Pt(9.5)
    p2.font.color.rgb = c_gray_light

    # Bottom-Left: CUSUM Drift Monitor
    add_card(slide4, Inches(0.8), Inches(4.5), gw, gh, "C. CUSUM Sequential Change Detection", border_color=c_amber)
    tb_q3 = slide4.shapes.add_textbox(Inches(1.0), Inches(5.0), gw - Inches(0.4), gh - Inches(0.6))
    tf_q3 = tb_q3.text_frame
    tf_q3.word_wrap = True
    p3 = tf_q3.paragraphs[0]
    p3.text = "• Defeating Crescendo Jailbreaks: S_n = max(0, S_n-1 + (X_n - mu - k))\n" \
              "• Cumulative Sum drift monitoring intercepts gradual multi-turn semantic steering that slips past single-turn classifiers."
    p3.font.size = Pt(9.5)
    p3.font.color.rgb = c_gray_light

    # Bottom-Right: Zero-Click Defanging
    add_card(slide4, Inches(6.9), Inches(4.5), gw, gh, "D. Steganography Neutralization & Zero-Click Defanging", border_color=c_emerald)
    tb_q4 = slide4.shapes.add_textbox(Inches(7.1), Inches(5.0), gw - Inches(0.4), gh - Inches(0.6))
    tf_q4 = tb_q4.text_frame
    tf_q4.word_wrap = True
    p4 = tf_q4.paragraphs[0]
    p4.text = "• Covert Channel Destruction: Strips zero-width characters (U+200B/C/D) and applies Unicode NFKC normalization.\n" \
              "• Markdown Defanging: Converts tracking pixels (![img](leak.com)) to safe text before human UI rendering."
    p4.font.size = Pt(9.5)
    p4.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 5: PILLAR 04 · LIVE DEMO EXECUTION PROOF
    # =========================================================================
    slide5 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide5)
    add_header(
        slide5,
        "Pillar 04 · Live Demo & Pitch (20% Weight)",
        "The 180-Second Live Execution: 3 Mediation Paths & Evidence Verification",
        "Live execution proof under 3 minutes — Zero videos, zero localhost excuses, 100% offline fallback."
    )

    # 4 Demo Cards
    dw = Inches(2.72)
    dh = Inches(5.0)

    # Demo 1: ALLOW
    add_card(slide5, Inches(0.8), Inches(1.8), dw, dh, "1. ALLOW Path", border_color=c_emerald)
    tb_d1 = slide5.shapes.add_textbox(Inches(0.95), Inches(2.4), dw - Inches(0.3), dh - Inches(0.8))
    tf_d1 = tb_d1.text_frame
    tf_d1.word_wrap = True
    p_d1 = tf_d1.paragraphs[0]
    p_d1.text = "• Tool: read_file('README.md')\n\n" \
                "• Invariants: Nominal read, all IFC checks valid.\n\n" \
                "• Verdict: ALLOW\n\n" \
                "• Result: Tool executes instantly (< 0.2ms) without friction."
    p_d1.font.size = Pt(9.5)
    p_d1.font.color.rgb = c_gray_light

    # Demo 2: BLOCK
    add_card(slide5, Inches(3.72), Inches(1.8), dw, dh, "2. BLOCK Path", border_color=c_red)
    tb_d2 = slide5.shapes.add_textbox(Inches(3.87), Inches(2.4), dw - Inches(0.3), dh - Inches(0.8))
    tf_d2 = tb_d2.text_frame
    tf_d2.word_wrap = True
    p_d2 = tf_d2.paragraphs[0]
    p_d2.text = "• Tool: bash('rm -rf /')\n\n" \
                "• Invariants: Critical filesystem destruction matched.\n\n" \
                "• Verdict: BLOCK\n\n" \
                "• Result: [threat:FS_DELETE_CRITICAL] Syscall vetoed before hitting OS kernel."
    p_d2.font.size = Pt(9.5)
    p_d2.font.color.rgb = c_gray_light

    # Demo 3: ESCALATE_HITL
    add_card(slide5, Inches(6.64), Inches(1.8), dw, dh, "3. ESCALATE Path", border_color=c_amber)
    tb_d3 = slide5.shapes.add_textbox(Inches(6.79), Inches(2.4), dw - Inches(0.3), dh - Inches(0.8))
    tf_d3 = tb_d3.text_frame
    tf_d3.word_wrap = True
    p_d3 = tf_d3.paragraphs[0]
    p_d3.text = "• Tool: read_file('../../../../etc/shadow')\n\n" \
                "• Invariants: Sensitive account database boundary.\n\n" \
                "• Verdict: ESCALATE_HITL\n\n" \
                "• Result: Suspends execution; issues request apr-xxxx for cryptographic operator approval."
    p_d3.font.size = Pt(9.5)
    p_d3.font.color.rgb = c_gray_light

    # Demo 4: MERKLE SEAL
    add_card(slide5, Inches(9.56), Inches(1.8), dw, dh, "4. Cryptographic Seal", border_color=c_cyan)
    tb_d4 = slide5.shapes.add_textbox(Inches(9.71), Inches(2.4), dw - Inches(0.3), dh - Inches(0.8))
    tf_d4 = tb_d4.text_frame
    tf_d4.word_wrap = True
    p_d4 = tf_d4.paragraphs[0]
    p_d4.text = "• Ledger: ProtectLedger.verify()\n\n" \
                "• Merkle Chaining: Decisions 1, 2, and 3 committed into leaf hashes.\n\n" \
                "• Audit Verdict: ok=True\n\n" \
                "• Tamper-Proof: Any post-hoc DB modification breaks head hash."
    p_d4.font.size = Pt(9.5)
    p_d4.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 6: PROPOSED TECH STACK & GOOGLE ADK INTEGRATION
    # =========================================================================
    slide6 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide6)
    add_header(
        slide6,
        "Pillar 01 · Technical Depth & Engineering Rigor",
        "Proposed Technology Stack & Google ADK Native Integration",
        "Enterprise-grade, modular, and natively compatible with the Google Agent Ecosystem."
    )

    # Left: Code Card
    add_card(slide6, Inches(0.8), Inches(1.8), Inches(5.8), Inches(5.0), "Native Google ADK Integration (3 Lines)", border_color=c_teal)
    tb_code = slide6.shapes.add_textbox(Inches(1.0), Inches(2.4), Inches(5.4), Inches(4.2))
    tf_code = tb_code.text_frame
    tf_code.word_wrap = True
    p_c = tf_code.paragraphs[0]
    p_c.text = "from google.adk import Agent\n" \
               "from panoptis.adapters.google_adk import PanoptisADKGuard\n\n" \
               "# 1. Initialize Sovereign Runtime Guard\n" \
               "guard = PanoptisADKGuard(policy_path='panoptis.yaml')\n\n" \
               "# 2. Wire directly into ADK lifecycle hooks\n" \
               "agent = Agent(\n" \
               "    model='gemini-2.0-flash',\n" \
               "    tools=[database_query, bash_exec, fetch_url],\n" \
               "    before_tool_callback=guard.before_tool_callback,\n" \
               "    after_tool_callback=guard.after_tool_callback,\n" \
               ")"
    p_c.font.name = "Courier"
    p_c.font.size = Pt(9)
    p_c.font.color.rgb = c_cyan

    # Right: Architecture Stack Card
    add_card(slide6, Inches(6.9), Inches(1.8), Inches(5.6), Inches(5.0), "Production Technology Stack", border_color=c_cyan)
    tb_stk = slide6.shapes.add_textbox(Inches(7.1), Inches(2.4), Inches(5.2), Inches(4.2))
    tf_stk = tb_stk.text_frame
    tf_stk.word_wrap = True
    stack_items = [
        ("Core Engine: ", "Python 3.12+ async hypervisor, Pydantic v2 schemas, FastAPI sidecars."),
        ("Security Logics: ", "Metric Temporal Logic (MTL) parser, Dual-Lattice meet/join algebra, CUSUM drift statistics."),
        ("Cryptographic Ledger: ", "RFC 8785 Canonical JSON, SHA-256 Merkle Trees, Post-Quantum ML-DSA / SLH-DSA signing."),
        ("Multi-Agent Adapters: ", "Google ADK, Claude Code (PreToolUse hook), LangChain/LangGraph, AutoGen, and MCP Gateway."),
        ("Persistence & Concurrency: ", "SQLite WAL (local dev) / PostgreSQL (psycopg[binary]) + pgvector, Redis broker."),
    ]
    for b, n in stack_items:
        p = tf_stk.add_paragraph() if tf_stk.paragraphs[0].text else tf_stk.paragraphs[0]
        p.space_after = Pt(8)
        rb = p.add_run()
        rb.text = "• " + b
        rb.font.bold = True
        rb.font.size = Pt(10)
        rb.font.color.rgb = c_white
        rn = p.add_run()
        rn.text = n
        rn.font.size = Pt(9.5)
        rn.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 7: PILLAR 03 · IMPACT, USERS & MONDAY MORNING ADOPTION
    # =========================================================================
    slide7 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide7)
    add_header(
        slide7,
        "Pillar 03 · Impact & Feasibility (20% Weight)",
        "Target Users, Market Relevance & Monday Morning Impact",
        "Who opens and uses Panoptis on Monday morning after the hackathon?"
    )

    # 3 Persona Cards
    add_card(slide7, Inches(0.8), Inches(1.8), card_w, Inches(3.2), "AI Platform Engineers", border_color=c_cyan)
    tb_p1 = slide7.shapes.add_textbox(Inches(1.0), Inches(2.4), card_w - Inches(0.4), Inches(2.4))
    p1 = tb_p1.text_frame.paragraphs[0]
    p1.text = "• Monday Problem: Teams want to give Gemini coding agents shell/database access, but fear accidental production drops.\n\n" \
              "• Panoptis Value: 3-line ADK drop-in with 0.3ms overhead. Enforces safe boundaries without breaking developer velocity."
    p1.font.size = Pt(9.5)
    p1.font.color.rgb = c_gray_light

    add_card(slide7, Inches(4.84), Inches(1.8), card_w, Inches(3.2), "Enterprise CISOs & Compliance", border_color=c_teal)
    tb_p2 = slide7.shapes.add_textbox(Inches(5.04), Inches(2.4), card_w - Inches(0.4), Inches(2.4))
    p2 = tb_p2.text_frame.paragraphs[0]
    p2.text = "• Monday Problem: Regulated industries (FinTech, Healthcare) face EU AI Act Article 12/14/15 human-oversight mandates.\n\n" \
              "• Panoptis Value: Exportable Merkle inclusion proofs, GDPR crypto-shredding, and auditable HITL approvals."
    p2.font.size = Pt(9.5)
    p2.font.color.rgb = c_gray_light

    add_card(slide7, Inches(8.88), Inches(1.8), card_w, Inches(3.2), "Autonomous Agent Builders", border_color=c_emerald)
    tb_p3 = slide7.shapes.add_textbox(Inches(9.08), Inches(2.4), card_w - Inches(0.4), Inches(2.4))
    p3 = tb_p3.text_frame.paragraphs[0]
    p3.text = "• Monday Problem: Autonomous loops get stuck oscillating parameters or consuming $1,000s in run-away token loops.\n\n" \
              "• Panoptis Value: Circuit Breaker FSM trips automatically, preventing cascade failures and cloud cost runaway."
    p3.font.size = Pt(9.5)
    p3.font.color.rgb = c_gray_light

    # Bottom Full-Width Economic Impact Card
    add_card(slide7, Inches(0.8), Inches(5.2), Inches(11.72), Inches(1.6), "Economic Impact & Distribution Strategy", border_color=c_amber)
    tb_imp = slide7.shapes.add_textbox(Inches(1.0), Inches(5.6), Inches(11.32), Inches(1.1))
    p_imp = tb_imp.text_frame.paragraphs[0]
    p_imp.text = "• Open Core Distribution: High-adoption open-source runtime hypervisor (pip install panoptis) driving bottom-up developer adoption.\n" \
                 "• Enterprise Monetization: Commercial SaaS / On-prem multi-tenant control plane with Google Chronicle streaming, SAML SSO, and fleet attestation.\n" \
                 "• Cost Reduction: Eliminates expensive LLM-as-a-judge secondary token calls ($0.03/eval -> $0.00 in-memory hypervisor)."
    p_imp.font.size = Pt(10)
    p_imp.font.color.rgb = c_white

    # =========================================================================
    # SLIDE 8: EMPIRICAL BENCHMARKS & SCIENTIFIC RIGOR
    # =========================================================================
    slide8 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide8)
    add_header(
        slide8,
        "Pillar 01 & 03 · Engineering Rigor & Feasibility",
        "Empirical Validation: Fixed 80-Case Incident Benchmark",
        "Continuous CI regression gates on balanced hard-negatives — No marketing illusions."
    )

    # Left: Benchmark Metrics Table
    add_card(slide8, Inches(0.8), Inches(1.8), Inches(6.0), Inches(5.0), "Published Empirical Measurements", border_color=c_cyan)
    tb_b = slide8.shapes.add_textbox(Inches(1.0), Inches(2.4), Inches(5.6), Inches(4.2))
    tf_b = tb_b.text_frame
    tf_b.word_wrap = True
    b_rows = [
        ("Attack Recall (30/40 Blocked): ", "75.0%", c_emerald, "Catches destructive deletions, reverse shells, exfil loops, credential dumping."),
        ("False Positive Rate (19/40 Flagged): ", "47.5%", c_red, "Published Defect: Tricky cleanup commands (rm -rf ./node_modules) flagged under strict policy."),
        ("Precision: ", "61.2%", c_cyan, "True positive ratio on balanced adversarial/benign incident corpus."),
        ("Inline Latency (p50): ", "0.3 ms", c_emerald, "Median evaluation overhead per tool call in production."),
        ("Inline Latency (p95): ", "0.7 ms", c_amber, "95th percentile latency across regex matching and taint shingling."),
    ]
    for lbl, val, col, desc in b_rows:
        p = tf_b.add_paragraph() if tf_b.paragraphs[0].text else tf_b.paragraphs[0]
        p.space_after = Pt(8)
        rb = p.add_run()
        rb.text = "• " + lbl
        rb.font.bold = True
        rb.font.size = Pt(10)
        rb.font.color.rgb = c_white
        rv = p.add_run()
        rv.text = val + "  "
        rv.font.bold = True
        rv.font.size = Pt(10)
        rv.font.color.rgb = col
        rd = p.add_run()
        rd.text = f"({desc})"
        rd.font.size = Pt(8.5)
        rd.font.color.rgb = c_gray_muted

    # Right: Honest Engineering Rigor Callout
    add_card(slide8, Inches(7.1), Inches(1.8), Inches(5.4), Inches(5.0), "Radical Transparency & Trade-offs", border_color=c_emerald)
    tb_t = slide8.shapes.add_textbox(Inches(7.3), Inches(2.4), Inches(5.0), Inches(4.2))
    tf_t = tb_t.text_frame
    tf_t.word_wrap = True
    t_points = [
        ("Why We Publish the 47.5% FP Rate: ", "Most hackathon teams claim '99.9% accuracy' using synthetic hello-world benchmarks. We test on hard negatives (e.g. `DROP TABLE IF EXISTS`, `rm -rf ./node_modules`). Our default strict policy prioritizes safety-first."),
        ("Automated CI Regression Gate: ", "The incident benchmark runs on every GitHub PR (python -m benchmarks.incidents --check). If accuracy or latency regresses, the build fails."),
        ("Physical Hardware Boundaries: ", "We document that crypto-shredding guarantees algorithmic unrecoverability, while physical SSD flash blocks are subject to controller wear-leveling."),
    ]
    for b, n in t_points:
        p = tf_t.add_paragraph() if tf_t.paragraphs[0].text else tf_t.paragraphs[0]
        p.space_after = Pt(10)
        rb = p.add_run()
        rb.text = "• " + b
        rb.font.bold = True
        rb.font.size = Pt(10.5)
        rb.font.color.rgb = c_white
        rn = p.add_run()
        rn.text = n
        rn.font.size = Pt(9.5)
        rn.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 9: IMPLEMENTATION ROADMAP (WEEKEND TO ENTERPRISE)
    # =========================================================================
    slide9 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide9)
    add_header(
        slide9,
        "Pillar 01 & 03 · Feasibility & Implementation Plan",
        "Implementation Roadmap: From Hackathon MVP to Enterprise Standard",
        "Pragmatic, milestone-driven execution plan backed by verified software."
    )

    rw = Inches(2.72)
    rh = Inches(5.0)

    # Phase 1
    add_card(slide9, Inches(0.8), Inches(1.8), rw, rh, "Phase 1: Working Core", border_color=c_emerald)
    tb_r1 = slide9.shapes.add_textbox(Inches(0.95), Inches(2.4), rw - Inches(0.3), rh - Inches(0.8))
    r1 = tb_r1.text_frame.paragraphs[0]
    r1.text = "COMPLETED & VERIFIED\n\n" \
              "• 546 Unit/Integration tests passing (100% green CI).\n\n" \
              "• 7-Stage pre-execution pipeline with 0.3ms latency.\n\n" \
              "• Native Google ADK adapter & Claude Code hook.\n\n" \
              "• Verified offline CLI demo & Merkle evidence ledger."
    r1.font.size = Pt(9.5)
    r1.font.color.rgb = c_gray_light

    # Phase 2
    add_card(slide9, Inches(3.72), Inches(1.8), rw, rh, "Phase 2: Cloud Fleet", border_color=c_cyan)
    tb_r2 = slide9.shapes.add_textbox(Inches(3.87), Inches(2.4), rw - Inches(0.3), rh - Inches(0.8))
    r2 = tb_r2.text_frame.paragraphs[0]
    r2.text = "MONTHS 1 – 3\n\n" \
              "• Kubernetes DaemonSet & Sidecar container for swarm pods.\n\n" \
              "• Google Cloud Chronicle SIEM live event streaming.\n\n" \
              "• Automated policy tuning reducing FP rate from 47% to < 5%.\n\n" \
              "• Slack/Discord approval bot integrations for HITL."
    r2.font.size = Pt(9.5)
    r2.font.color.rgb = c_gray_light

    # Phase 3
    add_card(slide9, Inches(6.64), Inches(1.8), rw, rh, "Phase 3: eBPF Hypervisor", border_color=c_teal)
    tb_r3 = slide9.shapes.add_textbox(Inches(6.79), Inches(2.4), rw - Inches(0.3), rh - Inches(0.8))
    r3 = tb_r3.text_frame.paragraphs[0]
    r3.text = "MONTHS 4 – 6\n\n" \
              "• Kernel-level eBPF intercept engine (< 50 microsecond latency).\n\n" \
              "• Machine-checked Coq/Isabelle proofs for Dual-Lattice Non-Interference.\n\n" \
              "• Hardware Enclave (Intel SGX / AMD SEV) remote attestation."
    r3.font.size = Pt(9.5)
    r3.font.color.rgb = c_gray_light

    # Phase 4
    add_card(slide9, Inches(9.56), Inches(1.8), rw, rh, "Phase 4: Swarm Consensus", border_color=c_amber)
    tb_r4 = slide9.shapes.add_textbox(Inches(9.71), Inches(2.4), rw - Inches(0.3), rh - Inches(0.8))
    r4 = tb_r4.text_frame.paragraphs[0]
    r4.text = "MONTHS 7 – 12\n\n" \
              "• Byzantine fault-tolerant consensus for multi-agent tool calls.\n\n" \
              "• Enterprise SaaS control plane with global policy sync.\n\n" \
              "• Standardization into Open Agent Safety Specification (OASS)."
    r4.font.size = Pt(9.5)
    r4.font.color.rgb = c_gray_light

    # =========================================================================
    # SLIDE 10: PILLAR 05 · CONCLUSION & JUDGE SCORECARD ALIGNMENT
    # =========================================================================
    slide10 = prs.slides.add_slide(blank_layout)
    set_slide_background(slide10)
    add_header(
        slide10,
        "Pillar 05 · UX Craft & Summary Scorecard",
        "Why Panoptis Wins: Direct Alignment with All 5 Judging Pillars",
        "High-Agency Engineering, 100% Executable Software, and Unquestionable Sovereign Security."
    )

    # 5 Scorecard Rows
    sc_data = [
        ("Pillar 01: Tech Architecture (25%)", "Custom 7-stage hypervisor, Dual-Lattice IFC, Metric Temporal Logic, CUSUM drift statistics, 546 green unit tests, 0.3ms latency.", c_cyan),
        ("Pillar 02: Idea & Originality (20%)", "Non-obvious insight: 'Never trust an agent to police itself'. Moving security from token filtering to deterministic pre-execution tool boundaries.", c_teal),
        ("Pillar 03: Impact & Scale (20%)", "Unlocks enterprise deployment for Gemini 2.0 / Google ADK swarms. Solves acute EU AI Act / SOC2 compliance bottlenecks for Fortune 500 CISOs.", c_emerald),
        ("Pillar 04: Live Demo & Pitch (20%)", "Crisp 180s live demonstration showing ALLOW, BLOCK (0.01ms veto), ESCALATE_HITL, and Merkle ledger verification with 100% offline fallbacks.", c_amber),
        ("Pillar 05: UX Craft & Polish (15%)", "3-line drop-in developer UX, clean terminal TUI, structured RFC 8785 evidence, zero layout shifts, and fail-closed safety defaults.", c_white),
    ]

    sc_top = Inches(1.8)
    row_h = Inches(0.92)
    for i, (p_title, p_desc, col) in enumerate(sc_data):
        y = sc_top + (i * row_h)
        add_card(slide10, Inches(0.8), y, Inches(11.72), Inches(0.82), border_color=col)
        tb_sc = slide10.shapes.add_textbox(Inches(1.0), y + Inches(0.1), Inches(11.32), Inches(0.65))
        tf_sc = tb_sc.text_frame
        tf_sc.word_wrap = True
        p = tf_sc.paragraphs[0]
        rb = p.add_run()
        rb.text = p_title + ":  "
        rb.font.bold = True
        rb.font.size = Pt(11)
        rb.font.color.rgb = col
        rn = p.add_run()
        rn.text = p_desc
        rn.font.size = Pt(10)
        rn.font.color.rgb = c_gray_light

    prs.save(filename)
    print(f"Presentation saved successfully: {Path(filename).resolve()} ({os.path.getsize(filename):,} bytes)")
    return str(Path(filename).resolve())


if __name__ == "__main__":
    create_deck()
