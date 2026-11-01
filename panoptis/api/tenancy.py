"""Resolve the tenant a request acts for from its credential (see docs/TENANCY.md).

A tenant is never chosen by the caller. ``X-Tenant-ID`` is accepted only as a selector
that repeats the credential's tenant; anything else is a 403.
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass

DEFAULT_TENANT = "default"
_TENANT_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class TenancyError(Exception):
    """The request's credential does not authorize the tenant it asked for."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    via: str  # "single-tenant" | "tenant-key-map" | "cloud"


def parse_tenant_keys(raw: str | None) -> dict[str, str]:
    """``AGENTWATCH_TENANT_KEYS``: ``tenant=key`` pairs separated by commas or newlines.

    Returns key → tenant. Invalid tenant ids, empty keys and a key mapped to two tenants
    are configuration errors (raised, so a bad mapping fails startup, not a request).
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for part in re.split(r"[,\n]", raw):
        part = part.strip()
        if not part:
            continue
        tenant, sep, key = part.partition("=")
        tenant, key = tenant.strip(), key.strip()
        if not sep or not key or not _TENANT_ID.match(tenant):
            raise ValueError(
                f"AGENTWATCH_TENANT_KEYS: invalid entry for tenant {tenant!r}; expected tenant=key"
            )
        if key in out and out[key] != tenant:
            raise ValueError("AGENTWATCH_TENANT_KEYS: the same key is mapped to two tenants")
        out[key] = tenant
    return out


def tenant_keys_from_env() -> dict[str, str]:
    return parse_tenant_keys(os.environ.get("AGENTWATCH_TENANT_KEYS"))


def match_key(supplied: str | None, keys: dict[str, str]) -> str | None:
    """The tenant of ``supplied`` in ``keys``, comparing every key in constant time."""
    if not supplied:
        return None
    found = None
    s = supplied.encode("utf-8")
    for key, tenant in keys.items():
        if hmac.compare_digest(s, key.encode("utf-8")):
            found = tenant
    return found


def check_selector(principal_tenant: str, header: str | None) -> None:
    if header is not None and header.strip() and header.strip() != principal_tenant:
        raise TenancyError(403, "X-Tenant-ID does not match the tenant of the supplied credential")


def resolve(
    *,
    cloud_mode: bool,
    api_key: str | None,
    tenant_header: str | None,
    tenant_keys: dict[str, str],
    cloud_lookup: object | None = None,
) -> Principal:
    """The authenticated tenant, or :class:`TenancyError`.

    ``cloud_lookup`` is a callable ``raw_key -> tenant_id | None`` (the tenant store).
    Authentication of the key itself (401) is the auth dependency's job; this function
    re-checks it, so the two can never disagree into a header fallback.
    """
    if cloud_mode:
        tenant = cloud_lookup(api_key) if (callable(cloud_lookup) and api_key) else None
        if tenant is None:
            raise TenancyError(401, "Invalid, revoked or missing API key")
        check_selector(tenant, tenant_header)
        return Principal(tenant, "cloud")
    if tenant_keys:
        tenant = match_key(api_key, tenant_keys)
        if tenant is not None:
            check_selector(tenant, tenant_header)
            return Principal(tenant, "tenant-key-map")
    check_selector(DEFAULT_TENANT, tenant_header)
    return Principal(DEFAULT_TENANT, "single-tenant")
