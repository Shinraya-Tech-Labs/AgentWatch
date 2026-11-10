"""
CMP — local entitlement storage for premium licensing.

After a successful checkout the backend issues a signed entitlement token
(see :mod:`panoptis.security.license`). The CLI persists that token under
``~/.agentwatch/config.toml`` and reloads + verifies it on subsequent premium
requests. PANOPTIS owns this file exclusively, so it is written wholesale
rather than merged.

Storing the token is not the security boundary — the token is verified
cryptographically on every load, so a tampered or hand-edited store simply
fails verification and falls back to free tier.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from panoptis.security.license import Entitlement, LicenseError, verify_entitlement

# Environment override for the config directory — primarily a testing seam so
# tests never touch a developer's real ``~/.agentwatch``.
_HOME_ENV = "AGENTWATCH_HOME"
_SECTION = "premium"
_TOKEN_KEY = "entitlement_token"  # noqa: S105 # nosec B105 — TOML key name, not a secret


def config_home() -> Path:
    """Return the PANOPTIS config directory (``~/.agentwatch`` by default)."""
    override = os.environ.get(_HOME_ENV)
    return Path(override) if override else Path.home() / ".agentwatch"


def config_path() -> Path:
    """Return the path to the licensing config file."""
    return config_home() / "config.toml"


def store_entitlement_token(token: str, *, path: Path | None = None) -> Path:
    """Persist an entitlement ``token`` to the config file, returning its path."""
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # PANOPTIS owns this file; a single string value needs no TOML writer dep.
    target.write_text(
        f"[{_SECTION}]\n{_TOKEN_KEY} = {_toml_escape(token)}\n",
        encoding="utf-8",
    )
    return target


def load_entitlement_token(*, path: Path | None = None) -> str | None:
    """Return the stored entitlement token, or ``None`` if not present."""
    target = path or config_path()
    if not target.exists():
        return None
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None
    token = data.get(_SECTION, {}).get(_TOKEN_KEY)
    return token if isinstance(token, str) else None


def load_entitlement(
    public_key: str,
    *,
    path: Path | None = None,
    machine_id: str | None = None,
) -> Entitlement | None:
    """Load and cryptographically verify the stored entitlement.

    Returns the verified :class:`Entitlement`, or ``None`` when no token is
    stored or it fails verification (expired, tampered, wrong machine) — i.e.
    the caller is treated as free tier.
    """
    token = load_entitlement_token(path=path)
    if token is None:
        return None
    try:
        return verify_entitlement(token, public_key, machine_id=machine_id)
    except LicenseError:
        return None


def clear_entitlement(*, path: Path | None = None) -> None:
    """Remove the stored entitlement, reverting to free tier."""
    target = path or config_path()
    target.unlink(missing_ok=True)


def _toml_escape(value: str) -> str:
    """Render a string as a TOML basic string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
