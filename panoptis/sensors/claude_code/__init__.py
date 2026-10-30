"""Claude Code sensor: stream-json output and transcript (.jsonl) files.

Each line becomes one observation carrying the ids the line declares (session id,
message id, tool_use ids, uuid/parentUuid for transcripts). Original timestamps are kept
when the line has one; otherwise ``observed_at`` is the sensor capture time for live
streams and *missing* for file imports.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from panoptis.evidence.model import ClockInfo, ObservationDraft, SensorRef, parse_ts

SENSOR_VERSION = "1"


def declared_ids_for(line: dict[str, Any]) -> dict[str, str]:
    ids: dict[str, str] = {}
    sid = line.get("session_id") or line.get("sessionId")
    if sid:
        ids["session_id"] = str(sid)
    if line.get("uuid"):
        ids["entry_uuid"] = str(line["uuid"])
    if line.get("parentUuid"):
        ids["parent_entry_uuid"] = str(line["parentUuid"])
    if line.get("parent_tool_use_id"):
        ids["parent_tool_use_id"] = str(line["parent_tool_use_id"])
    msg = line.get("message") or {}
    if isinstance(msg, dict):
        if msg.get("id"):
            ids["message_id"] = str(msg["id"])
        uses, results = [], []
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                uses.append(str(block["id"]))
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and block.get("tool_use_id")
            ):
                results.append(str(block["tool_use_id"]))
        if uses:
            ids["tool_use_ids"] = ",".join(uses)
        if results:
            ids["tool_result_ids"] = ",".join(results)
    return ids


def drafts_from_lines(
    lines: Iterable[str | dict[str, Any]],
    *,
    live: bool = False,
    tenant_id: str = "default",
    instance_id: str | None = None,
) -> list[ObservationDraft]:
    from datetime import UTC, datetime

    ref = SensorRef(
        "claude_code", SENSOR_VERSION, instance_id or f"claude-code-{uuid.uuid4().hex[:8]}"
    )
    drafts = []
    seq = 0
    for raw in lines:
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                line = {"type": "unparseable", "raw": raw[:4000]}
        else:
            line = raw
        seq += 1
        ts = line.get("timestamp")
        if ts:
            observed, clock = parse_ts(ts), ClockInfo(source="source", clock_id="claude_code")
        elif live:
            observed, clock = (
                datetime.now(UTC),
                ClockInfo(source="sensor", clock_id=ref.instance_id),
            )
        else:
            observed, clock = None, ClockInfo(source="missing")
        drafts.append(
            ObservationDraft(
                sensor=ref,
                source_kind=f"claude_code.{line.get('type', 'unknown')}",
                payload=line,
                source_seq=seq,
                observed_at=observed,
                clock=clock,
                declared_ids=declared_ids_for(line),
                tenant_id=tenant_id,
            )
        )
    return drafts


def drafts_from_file(path: str | Path, tenant_id: str = "default") -> list[ObservationDraft]:
    with Path(path).open(encoding="utf-8") as fh:
        return drafts_from_lines(
            fh, live=False, tenant_id=tenant_id, instance_id=f"claude-code-file:{Path(path).name}"
        )
