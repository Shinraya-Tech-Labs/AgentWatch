from __future__ import annotations

from panoptis.security.secret_scrubber import scrub_string, scrub_value


def test_github_pat():
    assert "ghp_[REDACTED_SECRET]" in scrub_string("token=ghp_" + "a" * 30)


def test_private_key():
    key = "-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----"
    assert scrub_string(key) == "[REDACTED_PRIVATE_KEY]"


def test_db_uri():
    assert "postgres://user:[REDACTED_PASSWORD]@host/db" == scrub_string("postgres://user:pass@host/db")


def test_recursive():
    data = {"token": "ghp_" + "a" * 30, "nested": ["mysql://u:p@h/db"]}
    out = scrub_value(data)
    assert "ghp_[REDACTED_SECRET]" in out["token"]
    assert "mysql://u:[REDACTED_PASSWORD]@h/db" in out["nested"][0]