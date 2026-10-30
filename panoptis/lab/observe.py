"""Run a program under observation and ingest what it recorded."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from panoptis.runs.segment import run_id_for
from panoptis.runtime.engine import Engine
from panoptis.sensors.base import read_ndjson


@dataclass
class ObserveResult:
    exit_code: int
    run_id: str | None
    native_run_id: str
    observations: int
    stdout: str
    stderr: str
    process: dict[str, Any]


def build_command(command: list[str]) -> list[str]:
    """Route Python scripts through the native launcher; run anything else as-is."""
    if not command:
        raise ValueError("empty command")
    exe = Path(command[0]).name.lower()
    if exe.startswith("python") or command[0] == sys.executable:
        rest = command[1:]
        if rest and rest[0].endswith(".py"):
            return [sys.executable, "-m", "panoptis.sensors.native.launch", *rest]
    if command[0].endswith(".py"):
        return [sys.executable, "-m", "panoptis.sensors.native.launch", *command]
    return command


def observe(
    engine: Engine,
    command: list[str],
    *,
    tenant_id: str = "default",
    env: dict[str, str] | None = None,
    native_run_id: str | None = None,
    timeout: float | None = None,
) -> ObserveResult:
    from panoptis.cli._utils.run_cmd import (  # repo convention: route subprocesses via cli._utils
        CommandError,
        run,
    )

    native_run_id = native_run_id or uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="panoptis-observe-") as tmp:
        out = Path(tmp) / "observations.ndjson"
        full_env = {
            **os.environ,
            **(env or {}),
            "AGENTWATCH_OBSERVE_FILE": str(out),
            "AGENTWATCH_RUN_ID": native_run_id,
            "AGENTWATCH_COMMAND": json.dumps(command),
            "PYTHONUNBUFFERED": "1",
        }
        try:
            proc = run(build_command(command), env=full_env, timeout=timeout)
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except CommandError as exc:
            if exc.returncode is None:  # validation or launch failure, not a program exit code
                raise
            code, stdout, stderr = exc.returncode, exc.stdout or "", exc.stderr or ""
        drafts = read_ndjson(out) if out.exists() else []
        for d in drafts:
            d.tenant_id = tenant_id
        engine.ingest(drafts)
    report = engine.process(tenant_id)
    run_id = run_id_for(tenant_id, ("native.run", native_run_id)) if drafts else None
    return ObserveResult(
        code, run_id, native_run_id, len(drafts), stdout or "", stderr or "", report
    )
