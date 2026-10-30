"""Egress network & SSRF guard for HTTP tool calls."""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable
from typing import Any


class SSRFBlockedError(PermissionError):
    """Raised when an egress URL targets private, loopback, or metadata networks."""


def _is_bad_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any((ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved))


class EgressGuard:
    """Restricts outbound HTTP tool requests to safe public networks and allowed domains."""

    BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "127.0.0.1", "::1"}

    def __init__(self, allowed_domains: list[str] | None = None) -> None:
        self.allowed_domains = allowed_domains

    def check_url(self, url: str, allowed_domains: list[str] | None = None) -> bool:
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urllib.parse.urlsplit(url.strip())
        except ValueError:
            return False
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        host = (parsed.hostname or "").strip("[]").lower()
        if not host or host in self.BLOCKED_HOSTNAMES or host.endswith((".local", ".internal")):
            return False

        domains = allowed_domains if allowed_domains is not None else self.allowed_domains
        if domains is not None:
            if not any(host == d.lower() or host.endswith("." + d.lower()) for d in domains):
                return False

        try:
            if _is_bad_ip(ipaddress.ip_address(host)):
                return False
        except ValueError:
            try:
                if _is_bad_ip(ipaddress.ip_address(socket.gethostbyname(host))):
                    return False
            except (socket.gaierror, OSError, ValueError):
                pass
        return True

    def verify(self, url: str, allowed_domains: list[str] | None = None) -> None:
        if not self.check_url(url, allowed_domains=allowed_domains):
            raise SSRFBlockedError(f"Egress request to '{url}' blocked by SSRF guard.")

    def guard(self, func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(url: str, *args: Any, **kwargs: Any) -> Any:
            self.verify(url)
            return func(url, *args, **kwargs)

        return wrapper
