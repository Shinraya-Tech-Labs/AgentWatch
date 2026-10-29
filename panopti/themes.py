"""Panopti Theme Engine: Customizable ANSI Color Palettes for CLI & Terminal Hypervisor.

Provides customizable aesthetic themes (Cyberpunk, Matrix, Amber, Monochrome/Plain)
and respects standard NO_COLOR (https://no-color.org) environment variables.
Pure standard library (Ponytail: Ultra).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def should_disable_color(no_color_flag: bool = False) -> bool:
    """Return True if colors should be suppressed via CLI flag or NO_COLOR environment."""
    if no_color_flag:
        return True
    no_color_env = os.environ.get("NO_COLOR")
    return bool(no_color_env is not None and no_color_env.strip() != "")


@dataclass(frozen=True)
class ThemePalette:
    """ANSI color definitions for a specific terminal theme palette."""

    name: str
    reset: str = "\033[0m"
    bold: str = "\033[1m"
    dim: str = "\033[2m"
    red: str = "\033[91m"
    green: str = "\033[92m"
    yellow: str = "\033[93m"
    blue: str = "\033[94m"
    magenta: str = "\033[95m"
    cyan: str = "\033[96m"
    white: str = "\033[97m"
    bg_red: str = "\033[41m"
    bg_green: str = "\033[42m"

    # Uppercase aliases for direct compatibility with legacy Colors class
    @property
    def RESET(self) -> str:
        return self.reset

    @property
    def BOLD(self) -> str:
        return self.bold

    @property
    def DIM(self) -> str:
        return self.dim

    @property
    def RED(self) -> str:
        return self.red

    @property
    def GREEN(self) -> str:
        return self.green

    @property
    def YELLOW(self) -> str:
        return self.yellow

    @property
    def BLUE(self) -> str:
        return self.blue

    @property
    def MAGENTA(self) -> str:
        return self.magenta

    @property
    def CYAN(self) -> str:
        return self.cyan

    @property
    def WHITE(self) -> str:
        return self.white

    @property
    def BG_RED(self) -> str:
        return self.bg_red

    @property
    def BG_GREEN(self) -> str:
        return self.bg_green

    def colorize(self, text: str, color_attr: str, enabled: bool = True) -> str:
        """Wrap text in a theme color if styling is enabled."""
        if not enabled or self.name in ("plain", "no-color", "monochrome"):
            return text
        code = getattr(self, color_attr, "")
        if not code:
            return text
        return f"{code}{text}{self.reset}"


CYBERPUNK = ThemePalette(
    name="cyberpunk",
    reset="\033[0m",
    bold="\033[1m",
    dim="\033[2m",
    red="\033[91m",
    green="\033[92m",
    yellow="\033[93m",
    blue="\033[94m",
    magenta="\033[95m",
    cyan="\033[96m",
    white="\033[97m",
    bg_red="\033[41m",
    bg_green="\033[42m",
)

MATRIX = ThemePalette(
    name="matrix",
    reset="\033[0m",
    bold="\033[1m",
    dim="\033[2m\033[32m",
    red="\033[91m",
    green="\033[92m",
    yellow="\033[92m",
    blue="\033[32m",
    magenta="\033[32m",
    cyan="\033[92m",
    white="\033[92m",
    bg_red="\033[41m",
    bg_green="\033[42m",
)

AMBER = ThemePalette(
    name="amber",
    reset="\033[0m",
    bold="\033[1m",
    dim="\033[2m\033[33m",
    red="\033[91m",
    green="\033[33m",
    yellow="\033[93m",
    blue="\033[33m",
    magenta="\033[33m",
    cyan="\033[93m",
    white="\033[93m",
    bg_red="\033[41m",
    bg_green="\033[43m",
)

PLAIN = ThemePalette(
    name="plain",
    reset="",
    bold="",
    dim="",
    red="",
    green="",
    yellow="",
    blue="",
    magenta="",
    cyan="",
    white="",
    bg_red="",
    bg_green="",
)

THEMES: Mapping[str, ThemePalette] = {
    "cyberpunk": CYBERPUNK,
    "matrix": MATRIX,
    "amber": AMBER,
    "monochrome": PLAIN,
    "plain": PLAIN,
    "no-color": PLAIN,
}


class ThemeManager:
    """Manages terminal aesthetic themes and NO_COLOR enforcement."""

    DEFAULT_THEME: str = "cyberpunk"
    THEMES: Mapping[str, ThemePalette] = THEMES

    @classmethod
    def get_theme(cls, name: str | None = None, *, no_color: bool = False) -> ThemePalette:
        """Resolve and return active ThemePalette respecting NO_COLOR."""
        if should_disable_color(no_color):
            return cls.THEMES["plain"]
        if not name:
            return cls.THEMES[cls.DEFAULT_THEME]
        key = name.strip().lower()
        return cls.THEMES.get(key, cls.THEMES[cls.DEFAULT_THEME])

    @classmethod
    def list_themes(cls) -> list[str]:
        """Return list of supported theme names."""
        return ["cyberpunk", "matrix", "amber", "monochrome", "plain"]


__all__ = [
    "AMBER",
    "CYBERPUNK",
    "MATRIX",
    "PLAIN",
    "THEMES",
    "ThemeManager",
    "ThemePalette",
    "should_disable_color",
]
