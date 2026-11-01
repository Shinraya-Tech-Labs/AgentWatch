"""Panopti ASCII Banner."""

from __future__ import annotations

import sys

PANOPTI_ASCII_BANNER = r"""
  ██████╗  █████╗ ███╗   ██╗ ██████╗ ██████╗ ████████╗██╗
  ██╔══██╗██╔══██╗████╗  ██║██╔═══██╗██╔══██╗╚══██╔══╝██║
  ██████╔╝███████║██╔██╗ ██║██║   ██║██████╔╝   ██║   ██║
  ██╔═══╝ ██╔══██║██║╚██╗██║██║   ██║██╔═══╝    ██║   ██║
  ██║     ██║  ██║██║ ╚████║╚██████╔╝██║        ██║   ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚═╝        ╚═╝   ╚═╝
     Argos Panoptes • All-Seeing Pre-Execution Guardrail
"""

PANOPTI_ASCII_BANNER_SAFE = r"""
  ____                                _   _ 
 |  _ \ __ _ _ __   ___  _ __  _   _ (_) | |
 | |_) / _` | '_ \ / _ \| '_ \| |_| | | | |
 |  __/ (_| | | | | (_) | |_) |  _  | | |_|
 |_|   \__,_|_| |_|\___/| .__/|_| |_|_| (_)
                        |_|                 
     Argos Panoptes - All-Seeing Pre-Execution Guardrail
"""


def print_banner() -> None:
    """Print banner safely across all terminal encodings (e.g. Windows cp1252/cp437)."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        PANOPTI_ASCII_BANNER.encode(encoding)
        sys.stdout.write(PANOPTI_ASCII_BANNER + "\n")
        sys.stdout.flush()
    except (UnicodeEncodeError, LookupError):
        sys.stdout.write(PANOPTI_ASCII_BANNER_SAFE + "\n")
        sys.stdout.flush()
