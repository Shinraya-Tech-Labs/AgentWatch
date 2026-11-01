#!/usr/bin/env python3
"""Check a Docker Compose deployment for unsafe service exposure (TO-009).

Usage::

    python scripts/check_deploy_security.py --env-file .env          # runs `docker compose config`
    python scripts/check_deploy_security.py --config-json config.json  # a rendered config
    python scripts/check_deploy_security.py --env-file .env --allow-public api,frontend

Errors (exit 1):
  * a data service (Postgres, Redis, Jaeger, other databases/brokers) published on an
    interface other than loopback;
  * an app port published on all interfaces without ``--allow-public``;
  * a Postgres password that is a known default or shorter than 12 characters;
  * Redis without ``--requirepass`` (or with a short/default one);
  * a privileged container.
Warnings: a container without ``no-new-privileges``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
DATA_IMAGE = re.compile(
    r"(?i)(postgres|pgvector|redis|valkey|mysql|mariadb|mongo|elasticsearch|opensearch|rabbitmq|jaeger|clickhouse|kafka)"
)
DEFAULT_PASSWORDS = {
    "panoptis",
    "agentwatch",
    "postgres",
    "password",
    "admin",
    "root",
    "secret",
    "changeme",
    "redis",
    "123456",
}
MIN_PASSWORD = 12


def _is_data(name: str, svc: dict[str, Any]) -> bool:
    return bool(DATA_IMAGE.search(svc.get("image", "") or "")) or name in (
        "postgres",
        "redis",
        "jaeger",
    )


def check(
    config: dict[str, Any], allow_public: set[str] | None = None
) -> tuple[list[str], list[str]]:
    allow_public = allow_public or set()
    errors: list[str] = []
    warnings: list[str] = []
    for name, svc in (config.get("services") or {}).items():
        for p in svc.get("ports") or []:
            if isinstance(p, str):  # short syntax "8000:8000" or "127.0.0.1:8000:8000"
                parts = p.split(":")
                host_ip = parts[0] if len(parts) == 3 else ""
                published = parts[-2] if len(parts) >= 2 else parts[0]
            else:
                host_ip, published = (
                    p.get("host_ip") or "",
                    str(p.get("published") or p.get("target")),
                )
            public = host_ip not in LOOPBACK
            if public and _is_data(name, svc):
                errors.append(
                    f"{name}: port {published} is published on {host_ip or 'all interfaces'}; data services must bind to 127.0.0.1"
                )
            elif public and name not in allow_public:
                errors.append(
                    f"{name}: port {published} is published on {host_ip or 'all interfaces'}; bind to 127.0.0.1 or pass --allow-public {name}"
                )
        if svc.get("privileged"):
            errors.append(f"{name}: runs privileged")
        if not any("no-new-privileges" in str(o) for o in svc.get("security_opt") or []):
            warnings.append(f"{name}: no-new-privileges is not set")
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) for e in env if "=" in e)
        image = svc.get("image", "") or ""
        if re.search(r"(?i)postgres|pgvector", image) or "POSTGRES_PASSWORD" in env:
            pw = str(env.get("POSTGRES_PASSWORD") or "")
            if not pw:
                errors.append(f"{name}: POSTGRES_PASSWORD is empty")
            elif pw.lower() in DEFAULT_PASSWORDS or len(pw) < MIN_PASSWORD:
                errors.append(
                    f"{name}: POSTGRES_PASSWORD is a default or shorter than {MIN_PASSWORD} characters"
                )
        if re.search(r"(?i)redis|valkey", image):
            cmd = svc.get("command") or []
            cmd = cmd if isinstance(cmd, list) else str(cmd).split()
            if "--requirepass" not in cmd:
                errors.append(f"{name}: Redis runs without --requirepass (unauthenticated)")
            else:
                pw = (
                    cmd[cmd.index("--requirepass") + 1]
                    if cmd.index("--requirepass") + 1 < len(cmd)
                    else ""
                )
                if pw.lower() in DEFAULT_PASSWORDS or len(pw) < MIN_PASSWORD:
                    errors.append(
                        f"{name}: the Redis password is a default or shorter than {MIN_PASSWORD} characters"
                    )
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check Compose service exposure and credentials")
    ap.add_argument("--config-json", help="output of `docker compose config --format json`")
    ap.add_argument("--env-file", help="env file passed to `docker compose config`")
    ap.add_argument("--file", default="docker-compose.yml")
    ap.add_argument(
        "--allow-public", default="", help="comma-separated services allowed on all interfaces"
    )
    args = ap.parse_args(argv)
    if args.config_json:
        with open(args.config_json, encoding="utf-8") as f:
            config = json.load(f)
    else:
        cmd = ["docker", "compose", "-f", args.file]
        if args.env_file:
            cmd += ["--env-file", args.env_file]
        cmd += ["--profile", "workers", "--profile", "tracing", "config", "--format", "json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - fixed argv
        if proc.returncode != 0:
            print(f"docker compose config failed: {proc.stderr.strip()[:500]}", file=sys.stderr)
            return 2
        config = json.loads(proc.stdout)
    errors, warnings = check(config, {s.strip() for s in args.allow_public.split(",") if s.strip()})
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
