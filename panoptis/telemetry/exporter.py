"""Offline exporters for session events (JSON Lines and CSV).

Both exporters use only the standard library and stream their input, so they can be
pointed at the ``event`` objects of a session replay (``panoptis session export``)
and the result loaded straight into pandas, DuckDB or a BI tool.

JSONL keeps every event exactly as given. CSV has a fixed header, so the file layout
never changes between runs: the common fields get their own column and everything
else goes into one trailing ``metadata_json`` column.
"""

from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable, Mapping
from enum import Enum
from typing import Any

CSV_COLUMNS: tuple[str, ...] = (
    "event_type",
    "timestamp",
    "tool_name",
    "confidence",
    "status",
    "session_id",
    "metadata_json",
)

# Keys that have their own CSV column. Everything else is kept in metadata_json.
_FLAT_KEYS = frozenset({"event_type", "timestamp", "status", "session_id"})

# A spreadsheet runs a cell as a formula when it starts with one of these characters.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize(value: str) -> str:
    """Stop spreadsheets from running agent-produced text as a formula."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else str(value)


def _confidence(value: Any) -> str:
    """Return the overall confidence score as text, or '' when there is none."""
    if isinstance(value, Mapping):
        value = value.get("overall_score")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    return repr(float(value))


def _tool_name(event: Mapping[str, Any]) -> str:
    for key in ("tool_call", "tool_result"):
        section = event.get(key)
        if isinstance(section, Mapping) and section.get("tool_name"):
            return _text(section["tool_name"])
    return ""


def flatten_event(event: Mapping[str, Any], session_id: str | None = None) -> dict[str, str]:
    """Turn one event into a row for :data:`CSV_COLUMNS`.

    Args:
        event: An event dict (the ``event`` part of a replay step).
        session_id: Used when the event has no ``session_id`` of its own.

    Returns:
        A dict with one text value for every column. Missing fields are ``""``.
    """
    metadata = {key: value for key, value in event.items() if key not in _FLAT_KEYS}
    row = {
        "event_type": _text(event.get("event_type")),
        "timestamp": _text(event.get("timestamp")),
        "tool_name": _tool_name(event),
        "confidence": _confidence(event.get("confidence")),
        "status": _text(event.get("status")),
        "session_id": _text(event.get("session_id") or session_id),
        "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str),
    }
    for column in CSV_COLUMNS:
        if column not in ("confidence", "metadata_json"):
            row[column] = _neutralize(row[column])
    return row


def export_to_jsonl(
    events: Iterable[Mapping[str, Any]], output_file: str | os.PathLike[str]
) -> int:
    """Write one JSON object per line.

    Args:
        events: Event dicts. Values that JSON cannot encode are written as text.
        output_file: Path of the file to create or overwrite (UTF-8).

    Returns:
        The number of events written.
    """
    count = 0
    with open(output_file, "w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, default=str))
            handle.write("\n")
            count += 1
    return count


def export_to_csv(
    events: Iterable[Mapping[str, Any]],
    output_file: str | os.PathLike[str],
    session_id: str | None = None,
) -> int:
    """Write events to a CSV file with the fixed :data:`CSV_COLUMNS` header.

    Args:
        events: Event dicts. Missing optional fields give empty cells.
        output_file: Path of the file to create or overwrite (UTF-8).
        session_id: Value for the ``session_id`` column when an event has none.

    Returns:
        The number of data rows written (the header is not counted).
    """
    count = 0
    with open(output_file, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for event in events:
            writer.writerow(flatten_event(event, session_id))
            count += 1
    return count
