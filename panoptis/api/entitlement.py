"""
Server-side premium entitlement enforcement.

Premium routes verify a signed entitlement token on the backend, so premium
results are never produced for a patched client that fakes a local check.
Opt-in: a no-op when no license public key is configured, fail-closed in
production.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, Header, HTTPException, status

from panoptis.security.license import (
    Entitlement,
    LicenseError,
    require_feature,
    verify_entitlement,
)

logger = logging.getLogger(__name__)


def _public_key() -> str | None:
    inline = os.environ.get("AGENTWATCH_LICENSE_PUBLIC_KEY")
    if inline:
        return inline

    key_file = os.environ.get("AGENTWATCH_LICENSE_PUBLIC_KEY_FILE")
    if key_file:
        try:
            return Path(key_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("License public key file not found: %s", key_file)
            return None
        except PermissionError:
            logger.error("Permission denied reading license public key file: %s", key_file)
            return None
        except UnicodeDecodeError:
            logger.error("Failed to decode license public key file %s: invalid encoding.", key_file)
            return None
        except OSError as exc:
            logger.error("OS error reading license public key file %s: %s", key_file, str(exc))
            return None
    return None


_LICENSE_PUBLIC_KEY: str | None = _public_key()
_ENV = os.getenv("AGENTWATCH_ENV") or os.getenv("ENVIRONMENT") or "development"
_IS_PROD = _ENV.lower() == "production"
_NOT_ENTITLED = status.HTTP_402_PAYMENT_REQUIRED


def entitlement_enforcement_enabled() -> bool:
    return _LICENSE_PUBLIC_KEY is not None


def authenticate_entitlement(
    x_entitlement_token: str | None = Header(default=None, alias="X-Entitlement-Token"),
    x_machine_id: str | None = Header(default=None, alias="X-Machine-Id"),
) -> Entitlement | None:
    """
    Verify the entitlement token, or return None when enforcement is off.

    This uses a bearer-token model where x_machine_id is passed as a metadata
    consistency check (sanity check) rather than cryptographic device binding or
    proof-of-possession. Since the client supplies both the token and the
    X-Machine-Id, anyone with a valid token can supply the matching machine ID.
    """
    if _LICENSE_PUBLIC_KEY is None:
        return None
    if not x_entitlement_token:
        raise HTTPException(_NOT_ENTITLED, "Premium entitlement required.")
    try:
        return verify_entitlement(x_entitlement_token, _LICENSE_PUBLIC_KEY, machine_id=x_machine_id)
    except LicenseError as exc:
        raise HTTPException(_NOT_ENTITLED, str(exc)) from exc


def require_entitlement(feature: str):
    """FastAPI dependency that gates a route on a premium feature."""

    def _dependency(
        entitlement: Entitlement | None = Depends(authenticate_entitlement),
    ) -> Entitlement | None:
        if _LICENSE_PUBLIC_KEY is None:
            if _IS_PROD:
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "AGENTWATCH_LICENSE_PUBLIC_KEY is required in production.",
                )
            return None
        try:
            return require_feature(entitlement, feature)
        except LicenseError as exc:
            raise HTTPException(_NOT_ENTITLED, str(exc)) from exc

    return _dependency
