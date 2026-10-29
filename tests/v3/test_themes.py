"""Unit tests for customizable ANSI color themes and NO_COLOR enforcement (Issue #766)."""

from __future__ import annotations

import io
import os
from unittest.mock import patch

from panopti.cli import main
from panopti.demo import run_interactive_demo
from panopti.themes import (
    AMBER,
    CYBERPUNK,
    MATRIX,
    PLAIN,
    ThemeManager,
    ThemePalette,
    should_disable_color,
)


def test_default_theme_is_cyberpunk():
    palette = ThemeManager.get_theme()
    assert palette.name == "cyberpunk"
    assert palette.cyan == "\033[96m"
    assert palette.reset == "\033[0m"
    assert palette.CYAN == "\033[96m"


def test_matrix_theme_palette():
    palette = ThemeManager.get_theme("matrix")
    assert palette.name == "matrix"
    assert "\033[92m" in palette.cyan
    assert palette.reset == "\033[0m"
    assert palette.GREEN == "\033[92m"


def test_amber_theme_palette():
    palette = ThemeManager.get_theme("amber")
    assert palette.name == "amber"
    assert "\033[93m" in palette.cyan
    assert palette.reset == "\033[0m"
    assert palette.YELLOW == "\033[93m"


def test_plain_and_monochrome_palettes():
    for name in ("plain", "no-color", "monochrome"):
        palette = ThemeManager.get_theme(name)
        assert palette.name == "plain"
        assert palette.cyan == ""
        assert palette.bold == ""
        assert palette.reset == ""
        assert palette.CYAN == ""
        assert palette.RESET == ""


def test_no_color_environment_variable():
    original = os.environ.get("NO_COLOR")
    try:
        os.environ["NO_COLOR"] = "1"
        assert should_disable_color() is True
        palette = ThemeManager.get_theme("cyberpunk")
        assert palette.name == "plain"
        assert palette.cyan == ""

        # Non-empty string triggers NO_COLOR
        os.environ["NO_COLOR"] = "true"
        assert ThemeManager.get_theme("matrix").name == "plain"

        # Explicit no-color flag
        assert ThemeManager.get_theme("amber", no_color=True).name == "plain"
    finally:
        if original is not None:
            os.environ["NO_COLOR"] = original
        else:
            os.environ.pop("NO_COLOR", None)


def test_no_color_empty_does_not_disable():
    original = os.environ.get("NO_COLOR")
    try:
        os.environ["NO_COLOR"] = ""
        assert should_disable_color() is False
        assert ThemeManager.get_theme("cyberpunk").name == "cyberpunk"
    finally:
        if original is not None:
            os.environ["NO_COLOR"] = original
        else:
            os.environ.pop("NO_COLOR", None)


def test_invalid_theme_name_fallbacks_to_cyberpunk():
    palette = ThemeManager.get_theme("unknown_invalid_theme")
    assert palette.name == "cyberpunk"
    assert palette.cyan == "\033[96m"


def test_theme_palette_colorize():
    palette = ThemeManager.get_theme("cyberpunk")
    styled = palette.colorize("alert", "red")
    assert styled == "\033[91malert\033[0m"

    plain = ThemeManager.get_theme("plain")
    assert plain.colorize("alert", "red") == "alert"


def test_theme_manager_list_themes():
    themes = ThemeManager.list_themes()
    assert "cyberpunk" in themes
    assert "matrix" in themes
    assert "amber" in themes
    assert "monochrome" in themes
    assert "plain" in themes


def test_demo_with_theme_matrix():
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        code = run_interactive_demo(fast=True, theme="matrix", no_color=True)
    assert code == 0
    out = buf.getvalue()
    assert "PANOPTI ACTIVE SENTINEL" in out
    assert "OUTCOME: PRODUCTION DATABASE SAVED" in out


def test_demo_with_theme_amber():
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        code = run_interactive_demo(fast=True, theme="amber", no_color=True)
    assert code == 0
    out = buf.getvalue()
    assert "PANOPTI ACTIVE SENTINEL" in out
    assert "OUTCOME: PRODUCTION DATABASE SAVED" in out


def test_cli_demo_with_theme_flag():
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        code = main(["demo", "--fast", "--theme", "matrix", "--no-color"])
    assert code == 0
    out = buf.getvalue()
    assert "PANOPTI ACTIVE SENTINEL" in out
    assert "TAMPER-PROOF AUDIT RECORD SEALED" in out
