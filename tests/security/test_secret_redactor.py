"""Tests for sensitive environment & API key exfiltration redactor in evidence envelopes (Issue #744)."""

from __future__ import annotations

import unittest
import uuid

from agentwatch.security.redaction import (
    SECRET_MASK,
    redact_secrets,
)


class TestSecretRedactor(unittest.TestCase):
    """Verify pattern matching, prefix preservation, nested scrubbing, and no false positives."""

    def test_redact_openai_project_key(self) -> None:
        key = "sk-proj-abc12345678901234567890"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"sk-proj-{SECRET_MASK}")
        self.assertNotIn("abc12345678901234567890", redacted)

    def test_redact_openai_legacy_key(self) -> None:
        key = "sk-abc12345678901234567890"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"sk-{SECRET_MASK}")
        self.assertNotIn("abc12345678901234567890", redacted)

    def test_redact_anthropic_key(self) -> None:
        key = "sk-ant-api03-abcdef123456789012345"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"sk-ant-{SECRET_MASK}")
        self.assertNotIn("api03-abcdef123456789012345", redacted)

    def test_redact_github_pat(self) -> None:
        key = "ghp_1234567890abcdef1234567890abcdef"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"ghp_{SECRET_MASK}")
        self.assertNotIn("1234567890abcdef1234567890abcdef", redacted)

    def test_redact_github_oauth(self) -> None:
        key = "gho_1234567890abcdef1234567890abcdef"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"gho_{SECRET_MASK}")
        self.assertNotIn("1234567890abcdef1234567890abcdef", redacted)

    def test_redact_aws_access_key(self) -> None:
        key = "AKIAIOSFODNN7EXAMPLE"
        redacted = redact_secrets(key)
        self.assertEqual(redacted, f"AKIA{SECRET_MASK}")
        self.assertNotIn("IOSFODNN7EXAMPLE", redacted)

    def test_redact_bearer_token(self) -> None:
        header = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.sflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        redacted = redact_secrets(header)
        self.assertEqual(redacted, f"Bearer {SECRET_MASK}")
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)

    def test_redact_nested_dict_payload(self) -> None:
        payload = {
            "credentials": {
                "openai": "sk-proj-abc12345678901234567890",
                "anthropic": "sk-ant-api03-abcdef123456789012345",
                "github": "ghp_1234567890abcdef1234567890abcdef",
                "aws": "AKIAIOSFODNN7EXAMPLE",
            },
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload",
            },
            "metadata": {
                "status": "active",
                "step_count": 5,
                "confidence": 0.95,
                "is_safe": True,
            },
        }
        scrubbed = redact_secrets(payload)
        self.assertEqual(scrubbed["credentials"]["openai"], f"sk-proj-{SECRET_MASK}")
        self.assertEqual(scrubbed["credentials"]["anthropic"], f"sk-ant-{SECRET_MASK}")
        self.assertEqual(scrubbed["credentials"]["github"], f"ghp_{SECRET_MASK}")
        self.assertEqual(scrubbed["credentials"]["aws"], f"AKIA{SECRET_MASK}")
        self.assertEqual(scrubbed["headers"]["Authorization"], f"Bearer {SECRET_MASK}")
        self.assertEqual(scrubbed["metadata"]["status"], "active")
        self.assertEqual(scrubbed["metadata"]["step_count"], 5)
        self.assertEqual(scrubbed["metadata"]["confidence"], 0.95)
        self.assertTrue(scrubbed["metadata"]["is_safe"])

    def test_redact_mixed_text_commands(self) -> None:
        cmd = (
            'curl -X POST https://api.openai.com/v1/chat -H "Authorization: Bearer my_secret_token_12345678" '
            '-H "X-Custom: ghp_1234567890abcdef1234567890abcdef" '
            '-d \'{"model": "gpt-4", "api_key": "sk-proj-12345678901234567890"}\''
        )
        scrubbed = redact_secrets(cmd)
        self.assertIn(f"Bearer {SECRET_MASK}", scrubbed)
        self.assertIn(f"ghp_{SECRET_MASK}", scrubbed)
        self.assertIn(f"sk-proj-{SECRET_MASK}", scrubbed)
        self.assertNotIn("my_secret_token_12345678", scrubbed)
        self.assertNotIn("1234567890abcdef", scrubbed)
        self.assertNotIn("12345678901234567890", scrubbed)

    def test_no_false_positives_uuid(self) -> None:
        sample_uuid = str(uuid.uuid4())
        self.assertEqual(redact_secrets(sample_uuid), sample_uuid)

        fixed_uuid = "c9a646d3-9c61-4cc9-bc77-d63f6221111e"
        self.assertEqual(redact_secrets(fixed_uuid), fixed_uuid)

    def test_no_false_positives_hashes(self) -> None:
        sha256_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        md5_hash = "5d41402abc4b2a76b9719d911017c592"
        git_sha = "d3554661ca2c890e20d11ab715c92de656a29ee7"

        self.assertEqual(redact_secrets(sha256_hash), sha256_hash)
        self.assertEqual(redact_secrets(md5_hash), md5_hash)
        self.assertEqual(redact_secrets(git_sha), git_sha)

    def test_no_false_positives_words(self) -> None:
        normal_words = "the sky is blue and the skater is bearing a heavy burden"
        self.assertEqual(redact_secrets(normal_words), normal_words)

    def test_idempotent_repeated_passes(self) -> None:
        payload = {
            "token": "sk-proj-abc12345678901234567890",
            "cmd": "curl -H 'Authorization: Bearer mytoken1234567890abcdef'",
        }
        pass1 = redact_secrets(payload)
        pass2 = redact_secrets(pass1)
        self.assertEqual(pass1, pass2)

    def test_non_string_preservation(self) -> None:
        self.assertIsNone(redact_secrets(None))
        self.assertEqual(redact_secrets(100), 100)
        self.assertEqual(redact_secrets(3.14), 3.14)
        self.assertEqual(redact_secrets(True), True)
        self.assertEqual(redact_secrets([]), [])
        self.assertEqual(redact_secrets({}), {})


if __name__ == "__main__":
    unittest.main()
