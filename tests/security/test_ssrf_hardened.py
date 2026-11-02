from __future__ import annotations

from unittest.mock import patch

from panoptis.security.ssrf_guard import is_safe_url


def test_blocks_localhost():
    with patch("panoptis.security.ssrf_guard.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 0))]):
        assert is_safe_url("http://example.com/") is False


def test_allows_public():
    with patch("panoptis.security.ssrf_guard.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        assert is_safe_url("http://example.com/") is True


def test_blocks_ipv6_loopback():
    with patch("panoptis.security.ssrf_guard.socket.getaddrinfo", return_value=[(None, None, None, None, ("::1", 0))]):
        assert is_safe_url("http://example.com/") is False