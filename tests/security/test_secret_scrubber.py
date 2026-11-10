from __future__ import annotations

from panoptis.security.secret_scrubber import scrub_string, scrub_value


def test_github_pat():
    assert "ghp_[REDACTED_SECRET]" in scrub_string("token=ghp_" + "a" * 30)


def test_aws_key():
    assert "AKIA[REDACTED_SECRET]" in scrub_string("AKIAIOSFODNN7EXAMPLE")


def test_openai_key():
    assert "sk-[REDACTED_SECRET]" in scrub_string("sk-" + "a" * 30)


def test_private_key():
    key = "-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----"
    assert scrub_string(key) == "[REDACTED_PRIVATE_KEY]"


def test_db_uri():
    assert "postgres://user:[REDACTED_PASSWORD]@host/db" == scrub_string("postgres://user:pass@host/db")
    assert "mysql://u:[REDACTED_PASSWORD]@h/db" == scrub_string("mysql://u:p@h/db")


def test_jwt():
    jwt = "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12
    assert "[REDACTED_JWT]" in scrub_string(jwt)


def test_recursive():
    data = {"token": "ghp_" + "a" * 30, "nested": ["mysql://u:p@h/db"]}
    out = scrub_value(data)
    assert "ghp_[REDACTED_SECRET]" in out["token"]
    assert "mysql://u:[REDACTED_PASSWORD]@h/db" in out["nested"][0]