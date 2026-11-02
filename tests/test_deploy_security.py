"""TO-009: deployment exposure checks (scripts/check_deploy_security.py)."""

# ruff: noqa: S603, S607 - the tests run fixed docker/helm/python commands, no external input

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_deploy_security", ROOT / "scripts" / "check_deploy_security.py"
)
cds = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(cds)

STRONG = "Zx9-strong-password-123"


def svc(**kw):
    base = {"security_opt": ["no-new-privileges:true"]}
    base.update(kw)
    return base


def test_the_insecure_layout_the_audit_found_is_rejected():
    old = {
        "services": {
            "postgres": {
                "image": "pgvector/pgvector:pg16",
                "environment": {"POSTGRES_PASSWORD": "panoptis"},
                "ports": ["5432:5432"],
            },
            "redis": {"image": "redis:7-alpine", "ports": ["6379:6379"]},
            "api": {"image": "api", "ports": ["8000:8000"]},
        }
    }
    errors, warnings = cds.check(old)
    text = "\n".join(errors)
    assert "postgres: port 5432" in text and "redis: port 6379" in text and "api: port 8000" in text
    assert "POSTGRES_PASSWORD is a default" in text
    assert "without --requirepass" in text
    assert len(warnings) == 3


def test_a_hardened_layout_passes():
    ok = {
        "services": {
            "postgres": svc(
                image="pgvector/pgvector:pg16",
                environment={"POSTGRES_PASSWORD": STRONG},
                ports=[{"host_ip": "127.0.0.1", "published": "5432", "target": 5432}],
            ),
            "redis": svc(
                image="redis:7-alpine",
                command=["redis-server", "--requirepass", STRONG],
                ports=[{"host_ip": "127.0.0.1", "published": "6379"}],
            ),
            "api": svc(image="api", ports=[{"host_ip": "127.0.0.1", "published": "8000"}]),
        }
    }
    assert cds.check(ok) == ([], [])


def test_public_app_port_needs_explicit_opt_in_but_data_services_never_do():
    cfg = {
        "services": {
            "api": svc(image="api", ports=[{"host_ip": "0.0.0.0", "published": "8000"}]),
            "redis": svc(
                image="redis:7",
                command=["redis-server", "--requirepass", STRONG],
                ports=[{"host_ip": "0.0.0.0", "published": "6379"}],
            ),
        }
    }
    errors, _ = cds.check(cfg, allow_public={"api", "redis"})
    assert errors == [
        "redis: port 6379 is published on 0.0.0.0; data services must bind to 127.0.0.1"
    ]


@pytest.mark.parametrize("pw", ["", "short", "postgres", "changeme"])
def test_weak_postgres_passwords(pw):
    cfg = {
        "services": {"postgres": svc(image="postgres:16", environment={"POSTGRES_PASSWORD": pw})}
    }
    assert cds.check(cfg)[0]


def test_short_redis_password_and_privileged_container():
    cfg = {
        "services": {
            "redis": svc(image="redis", command=["redis-server", "--requirepass", "abc"]),
            "x": svc(image="x", privileged=True),
        }
    }
    errors = "\n".join(cds.check(cfg)[0])
    assert "Redis password is a default or shorter" in errors and "x: runs privileged" in errors


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_the_repositorys_compose_file_passes(tmp_path):
    env = tmp_path / "check.env"
    env.write_text(
        f"AGENTWATCH_API_KEY=k\nPOSTGRES_PASSWORD={STRONG}\nREDIS_PASSWORD={STRONG}\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "check_deploy_security.py"),
            "--file",
            str(ROOT / "docker-compose.yml"),
            "--env-file",
            str(env),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 error(s), 0 warning(s)" in proc.stdout


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_compose_refuses_to_render_without_the_secrets(tmp_path):
    env = tmp_path / "empty.env"
    env.write_text("AGENTWATCH_API_KEY=k\n", encoding="utf-8")
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "docker-compose.yml"),
            "--env-file",
            str(env),
            "config",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0 and (
        "POSTGRES_PASSWORD" in proc.stderr or "REDIS_PASSWORD" in proc.stderr
    )


def test_config_json_input(tmp_path):
    f = tmp_path / "cfg.json"
    f.write_text(
        json.dumps({"services": {"redis": {"image": "redis", "ports": ["6379:6379"]}}}),
        encoding="utf-8",
    )
    assert cds.main(["--config-json", str(f)]) == 1


HELM = shutil.which("helm")


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_helm_chart_requires_a_secret_and_carries_no_password():
    bad = subprocess.run(
        [HELM, "template", "rel", str(ROOT / "helm")], capture_output=True, text=True, check=False
    )
    assert bad.returncode != 0 and "secrets.existingSecret is required" in bad.stderr
    ok = subprocess.run(
        [HELM, "template", "rel", str(ROOT / "helm"), "--set", "secrets.existingSecret=s"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr
    out = ok.stdout
    assert (
        "runAsNonRoot: true" in out
        and "allowPrivilegeEscalation: false" in out
        and "secretRef:" in out
    )
    assert "panoptis:panoptis@" not in out and "password" not in out.lower().replace(
        "passwords", ""
    )


def test_helm_values_have_no_default_password():
    import yaml

    values = yaml.safe_load((ROOT / "helm" / "values.yaml").read_text(encoding="utf-8"))
    assert "password" not in values.get("postgres", {})
    assert values["secrets"]["existingSecret"] == ""
