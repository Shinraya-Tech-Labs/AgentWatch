"""Unit tests for Panopti domain guard profiles."""

from panopti.presets.profiles import PROFILES, get_profile


def test_profiles_registered():
    assert "fintech" in PROFILES
    assert "devops" in PROFILES
    assert "healthcare" in PROFILES
    assert "support" in PROFILES


def test_fintech_profile():
    p = get_profile("fintech")
    assert p.confidence_floor == 0.75
    assert "idempotency_key" in p.required_keys
    assert p.max_argument_values["amount"] == 10000.0


def test_devops_profile():
    p = get_profile("devops")
    assert p.confidence_floor == 0.50
    assert any("DROP" in pat for pat in p.destructive_patterns)
    assert any("docker" in pat for pat in p.destructive_patterns)


def test_fallback_profile():
    p = get_profile("unknown_profile_name")
    assert p.name == "devops"
