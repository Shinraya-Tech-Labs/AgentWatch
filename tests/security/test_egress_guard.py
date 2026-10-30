"""Unit tests for EgressGuard in Panopti & AgentWatch."""

from __future__ import annotations

import unittest

from agentwatch.security import EgressGuard as AWEgressGuard
from agentwatch.security import SSRFBlockedError as AWSSRFBlockedError
from panopti.egress import EgressGuard, SSRFBlockedError


class TestEgressGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = EgressGuard()

    def test_empty_and_invalid_urls(self) -> None:
        self.assertFalse(self.guard.check_url(""))
        self.assertFalse(self.guard.check_url("   "))
        self.assertFalse(self.guard.check_url("not-a-url"))

    def test_schema_validation_rejects_non_http_https(self) -> None:
        self.assertFalse(self.guard.check_url("file:///etc/passwd"))
        self.assertFalse(self.guard.check_url("ftp://example.com/file"))
        self.assertFalse(self.guard.check_url("gopher://example.com/"))
        self.assertFalse(self.guard.check_url("javascript:alert(1)"))

    def test_cloud_metadata_ip_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(
            self.guard.check_url(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )
        )

    def test_cloud_metadata_ip_with_port_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://169.254.169.254:8080/metadata"))

    def test_localhost_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://localhost:6379/"))
        self.assertFalse(self.guard.check_url("https://localhost/admin"))

    def test_loopback_ipv4_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://127.0.0.1:2375/"))
        self.assertFalse(self.guard.check_url("http://127.0.0.1/status"))

    def test_loopback_ipv4_range_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://127.0.1.1/"))
        self.assertFalse(self.guard.check_url("http://127.128.0.1:9000/"))

    def test_loopback_ipv6_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://[::1]:8080/"))
        self.assertFalse(self.guard.check_url("http://[::1]/"))

    def test_rfc1918_10_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://10.0.0.1/"))
        self.assertFalse(self.guard.check_url("http://10.254.1.5:8000/api"))

    def test_rfc1918_172_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://172.16.0.1/"))
        self.assertFalse(self.guard.check_url("http://172.31.255.254/"))

    def test_rfc1918_192_blocked(self) -> None:
        self.assertFalse(self.guard.check_url("http://192.168.0.1/"))
        self.assertFalse(self.guard.check_url("http://192.168.1.100:3000/"))

    def test_public_safe_url_allowed_without_allowlist(self) -> None:
        self.assertTrue(self.guard.check_url("https://api.github.com/repos"))
        self.assertTrue(self.guard.check_url("http://93.184.216.34/"))

    def test_allowed_domains_permit_exact_match(self) -> None:
        guard = EgressGuard(allowed_domains=["api.github.com", "huggingface.co"])
        self.assertTrue(guard.check_url("https://api.github.com/repos/octocat"))
        self.assertTrue(guard.check_url("https://huggingface.co/models"))

    def test_allowed_domains_permit_subdomain(self) -> None:
        guard = EgressGuard(allowed_domains=["github.com"])
        self.assertTrue(guard.check_url("https://api.github.com/users"))
        self.assertTrue(guard.check_url("https://raw.github.com/file"))

    def test_allowed_domains_reject_foreign_domain(self) -> None:
        guard = EgressGuard(allowed_domains=["api.github.com"])
        self.assertFalse(guard.check_url("https://evil-site.com/steal"))
        self.assertFalse(guard.check_url("https://huggingface.co/models"))

    def test_method_allowed_domains_override(self) -> None:
        guard = EgressGuard(allowed_domains=["api.github.com"])
        self.assertTrue(
            guard.check_url("https://huggingface.co/api", allowed_domains=["huggingface.co"])
        )
        self.assertFalse(
            guard.check_url("https://api.github.com/repos", allowed_domains=["huggingface.co"])
        )

    def test_verify_raises_ssrf_blocked_error(self) -> None:
        with self.assertRaises(SSRFBlockedError):
            self.guard.verify("http://169.254.169.254/latest/meta-data/")
        # Should not raise for valid public URL
        self.guard.verify("https://api.github.com")

    def test_guard_decorator(self) -> None:
        fetched: list[str] = []

        @self.guard.guard
        def http_get(url: str) -> str:
            fetched.append(url)
            return "200 OK"

        self.assertEqual(http_get("https://api.github.com/zen"), "200 OK")
        self.assertEqual(fetched, ["https://api.github.com/zen"])

        with self.assertRaises(SSRFBlockedError):
            http_get("http://127.0.0.1:8000/secrets")

    def test_agentwatch_security_import_alias(self) -> None:
        self.assertIs(AWEgressGuard, EgressGuard)
        self.assertIs(AWSSRFBlockedError, SSRFBlockedError)


if __name__ == "__main__":
    unittest.main()
