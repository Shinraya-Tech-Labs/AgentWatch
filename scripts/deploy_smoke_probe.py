#!/usr/bin/env python3
# ruff: noqa: S603, S607 - fixed docker CLI commands; container names come from --project
"""Probe a running Compose deployment for exposure and credential problems (TO-009).

    python scripts/deploy_smoke_probe.py .env [--project panoptis]
        [--postgres 5432 --redis 6379 --api 8000 --frontend 3000]

Checks: Redis refuses unauthenticated and default-password access; Postgres rejects the old
default password; no published port answers on the host's LAN address; the API is healthy,
rejects requests without the key and accepts it; app containers run non-root with no
effective capabilities and no-new-privileges. Exit 0 only when every check passes.
Credentials are read from the env file and never printed.
"""

import argparse
import socket
import subprocess
import sys

import httpx
import psycopg

ap = argparse.ArgumentParser()
ap.add_argument("env_file")
ap.add_argument("--project", default="panoptis")
for name, default in (("postgres", 5432), ("redis", 6379), ("api", 8000), ("frontend", 3000)):
    ap.add_argument(f"--{name}", type=int, default=default)
args = ap.parse_args()
ENV = dict(line.strip().split("=", 1) for line in open(args.env_file, encoding="utf-8") if "=" in line and not line.lstrip().startswith("#"))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def redis_cmd(host, port, payload):
    with socket.create_connection((host, port), timeout=3) as s:
        s.sendall(payload)
        return s.recv(200).decode(errors="replace").strip()


# 1. Redis requires the password
r = redis_cmd("127.0.0.1", args.redis, b"PING\r\n")
check("redis rejects unauthenticated PING", r.startswith("-NOAUTH"), r)
pw = ENV["REDIS_PASSWORD"].encode()
r = redis_cmd("127.0.0.1", args.redis, b"AUTH " + pw + b"\r\nPING\r\n")
check("redis accepts the configured password", "+PONG" in r, r.replace("\r\n", " "))
r = redis_cmd("127.0.0.1", args.redis, b"AUTH panoptis\r\n")
check("redis rejects a default password", r.startswith("-WRONGPASS") or r.startswith("-ERR"), r)

# 2. Postgres rejects the old default credentials
try:
    psycopg.connect(f"host=127.0.0.1 port={args.postgres} user=panoptis password=panoptis dbname=panoptis connect_timeout=5").close()
    check("postgres rejects default password", False, "connected with panoptis/panoptis")
except psycopg.OperationalError as e:
    check("postgres rejects default password", "password authentication failed" in str(e), str(e).splitlines()[0][:90])
with psycopg.connect(f"host=127.0.0.1 port={args.postgres} user=panoptis password={ENV['POSTGRES_PASSWORD']} dbname=panoptis connect_timeout=5") as c:
    check("postgres accepts the configured password", c.execute("select 1").fetchone() == (1,))

# 3. Nothing is reachable on a non-loopback interface
probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
probe.connect(("10.255.255.255", 1))  # no packet is sent; picks the LAN interface address
lan_ip = probe.getsockname()[0]
probe.close()
for port in (args.postgres, args.redis, args.api, args.frontend):
    try:
        socket.create_connection((lan_ip, port), timeout=2).close()
        reachable = True
    except OSError:
        reachable = False
    check(f"port {port} unreachable on LAN address", not reachable, lan_ip)

# 4. API is up, requires the key
base = f"http://127.0.0.1:{args.api}"
check("api healthy", httpx.get(f"{base}/health", timeout=10).status_code == 200)
check("api rejects requests without the key", httpx.get(f"{base}/api/v3/status", timeout=10).status_code == 401)
check("api accepts the key", httpx.get(f"{base}/api/v3/status", headers={"X-Api-Key": ENV["AGENTWATCH_API_KEY"]}, timeout=10).status_code == 200)
check("dashboard serves", httpx.get(f"http://127.0.0.1:{args.frontend}/", timeout=20).status_code == 200)

# 5. Least privilege inside the app containers
for ctr in (f"{args.project}-api-1", f"{args.project}-frontend-1"):
    uid = subprocess.run(["docker", "exec", ctr, "id", "-u"], capture_output=True, text=True).stdout.strip()
    caps = subprocess.run(["docker", "exec", ctr, "sh", "-c", "grep CapEff /proc/1/status"], capture_output=True, text=True).stdout.split()
    check(f"{ctr} runs as non-root", uid not in ("", "0"), f"uid={uid}")
    check(f"{ctr} has no effective capabilities", caps[-1:] == ["0000000000000000"], " ".join(caps))
    nnp = subprocess.run(["docker", "inspect", ctr, "--format", "{{json .HostConfig.SecurityOpt}}"], capture_output=True, text=True).stdout.strip()
    check(f"{ctr} no-new-privileges", "no-new-privileges" in nnp, nnp)

print(f"\n{sum(ok for _, ok in results)}/{len(results)} checks passed")
sys.exit(0 if all(ok for _, ok in results) else 1)
