from __future__ import annotations

from panoptis.protect.formal_verifier import (
    PolicyFormalVerifier,
    VerificationStatus,
)
from panoptis.protect.rbac import RBACPolicy, parse_policy


def test_verifier_detects_fail_open_default_allow():
    policy_doc = {
        "version": "1.0",
        "default": "allow",
        "agents": {"bot": ["role1"]},
        "roles": {
            "role1": {
                "allowed_tools": ["read_file"],
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert cert.status == VerificationStatus.VULNERABILITY_DETECTED
    assert any(f.rule_id == "INV_DEFAULT_ALLOW" for f in cert.findings)


def test_verifier_detects_dead_allow_rule_subsumption():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"bot": ["role1"]},
        "roles": {
            "role1": {
                "allowed_tools": ["execute_shell"],
                "forbidden_tools": ["*"],  # '*' subsumes execute_shell
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert any(f.rule_id == "RULE_SUBSUMPTION_DEAD_ALLOW" for f in cert.findings)


def test_verifier_detects_redundant_allow_pattern():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"bot": ["role1"]},
        "roles": {
            "role1": {
                "allowed_tools": ["*", "read_file"],
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert any(f.rule_id == "RULE_SUBSUMPTION_REDUNDANT_ALLOW" for f in cert.findings)


def test_verifier_detects_path_starvation_contradiction():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"bot": ["role1"]},
        "roles": {
            "role1": {
                "allowed_tools": ["read_file"],
                "restricted_arguments": {
                    "read_file": {
                        "allowed_paths": ["/workspace/**"],
                        "blocked_paths": ["/**"],  # total starvation
                    }
                },
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert cert.status == VerificationStatus.CONTRADICTION_DETECTED
    assert any(f.rule_id == "CONTRADICTION_PATH_STARVATION" for f in cert.findings)


def test_verifier_detects_numeric_field_unsat_contradiction():
    from panoptis.protect.rbac import ArgumentRestriction, Role

    policy = RBACPolicy(
        version="1.0",
        default="deny",
        agents={"bot": ["role1"]},
        roles={
            "role1": Role(
                name="role1",
                allowed_tools=["calculate"],
                forbidden_tools=[],
                restrictions={
                    "calculate": ArgumentRestriction(
                        allowed_paths=[],
                        blocked_paths=[],
                        allowed_commands=[],
                        blocked_commands=[],
                        fields={"limit": {"min": 100, "max": 10}},
                    )
                },
            )
        },
        digest="test-digest",
        tenants={},
    )
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert cert.status == VerificationStatus.CONTRADICTION_DETECTED
    assert any(f.rule_id == "CONTRADICTION_NUMERIC_FIELD_UNSAT" for f in cert.findings)


def test_verifier_detects_unbounded_shell_access():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"coder": ["dev"]},
        "roles": {
            "dev": {
                "allowed_tools": ["execute_shell"],  # No restrictions at all!
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert cert.status == VerificationStatus.VULNERABILITY_DETECTED
    assert any(f.rule_id == "THEORETIC_BYPASS_UNBOUNDED_SHELL" for f in cert.findings)
    assert len(cert.counterexamples) > 0


def test_verifier_proves_hardened_policy_sound():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"coder": ["dev"]},
        "roles": {
            "dev": {
                "allowed_tools": ["execute_shell"],
                "restricted_arguments": {
                    "execute_shell": {
                        "allowed_commands": ["pytest", "git", "ls"],
                        "blocked_commands": ["rm -rf*", "sudo*", "curl*|*sh*", "bash*", "sh*"],
                        "allowed_paths": ["./**", "/workspace/**"],
                        "blocked_paths": ["**/.env*", "**/.ssh/**", "/etc/shadow*", "/etc/passwd*"],
                    }
                },
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    assert cert.is_sound
    assert cert.status == VerificationStatus.PROVEN_SOUND
    assert cert.invariants_passed >= cert.invariants_checked
    assert len(cert.counterexamples) == 0


def test_verifier_synthesizes_hardening_invariants():
    policy_doc = {
        "version": "1.0",
        "default": "deny",
        "agents": {"coder": ["dev"]},
        "roles": {
            "dev": {
                "allowed_tools": ["execute_shell"],
            }
        },
    }
    policy = parse_policy(policy_doc)
    verifier = PolicyFormalVerifier()
    cert = verifier.verify(policy)

    synth = cert.synthesized_invariants
    assert "dev:execute_shell" in synth["recommended_restrictions"]
    rec = synth["recommended_restrictions"]["dev:execute_shell"]
    assert "rm -rf*" in rec["blocked_commands"]
    assert "./**" in rec["allowed_paths"]
