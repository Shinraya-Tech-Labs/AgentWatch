"""Tests for the JSONL and CSV telemetry exporters."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from panoptis.core.schema import AgentEvent, ConfidenceData, EventType, ExecutionStatus
from panoptis.telemetry.exporter import (
    CSV_COLUMNS,
    export_to_csv,
    export_to_jsonl,
    flatten_event,
)

FULL_EVENT = {
    "event_id": "e1",
    "session_id": "s1",
    "event_type": "tool.call",
    "status": "success",
    "timestamp": "2026-06-14T10:00:00Z",
    "tool_call": {"tool_name": "bash", "raw_command": "echo hi"},
    "confidence": {"overall_score": 0.75, "explanation": "ok"},
    "step_number": 3,
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_jsonl_writes_one_object_per_line_and_returns_count(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    events = [FULL_EVENT, {"event_type": "agent.end", "session_id": "s1"}]

    assert export_to_jsonl(events, out) == 2

    lines = out.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == events


def test_jsonl_accepts_a_str_path_and_a_generator(tmp_path: Path) -> None:
    out = tmp_path / "gen.jsonl"
    count = export_to_jsonl((dict(FULL_EVENT, step_number=i) for i in range(5)), str(out))
    assert count == 5
    assert len(out.read_text(encoding="utf-8").splitlines()) == 5


def test_jsonl_empty_input_creates_an_empty_file(tmp_path: Path) -> None:
    out = tmp_path / "empty.jsonl"
    assert export_to_jsonl([], out) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_jsonl_keeps_unicode_and_writes_unserializable_values_as_text(tmp_path: Path) -> None:
    out = tmp_path / "u.jsonl"
    when = datetime(2026, 6, 14, 10, 0, tzinfo=UTC)
    export_to_jsonl([{"event_type": "x", "note": "naïve ✓ 日本", "at": when}], out)

    raw = out.read_text(encoding="utf-8")
    assert "naïve ✓ 日本" in raw
    assert json.loads(raw)["at"] == str(when)


def test_jsonl_overwrites_an_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "o.jsonl"
    out.write_text("old\nold\nold\n", encoding="utf-8")
    export_to_jsonl([FULL_EVENT], out)
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1


def test_csv_has_the_fixed_header_and_flattens_the_common_fields(tmp_path: Path) -> None:
    out = tmp_path / "events.csv"

    assert export_to_csv([FULL_EVENT], out) == 1

    with open(out, encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == list(CSV_COLUMNS)
    assert CSV_COLUMNS == (
        "event_type",
        "timestamp",
        "tool_name",
        "confidence",
        "status",
        "session_id",
        "metadata_json",
    )
    (row,) = _read_csv(out)
    assert row["event_type"] == "tool.call"
    assert row["timestamp"] == "2026-06-14T10:00:00Z"
    assert row["tool_name"] == "bash"
    assert row["confidence"] == "0.75"
    assert row["status"] == "success"
    assert row["session_id"] == "s1"


def test_csv_metadata_json_keeps_everything_else(tmp_path: Path) -> None:
    out = tmp_path / "m.csv"
    export_to_csv([FULL_EVENT], out)
    (row,) = _read_csv(out)

    metadata = json.loads(row["metadata_json"])
    assert metadata["event_id"] == "e1"
    assert metadata["step_number"] == 3
    assert metadata["tool_call"]["raw_command"] == "echo hi"
    assert "event_type" not in metadata
    assert "session_id" not in metadata


def test_csv_missing_optional_fields_give_empty_cells(tmp_path: Path) -> None:
    out = tmp_path / "sparse.csv"
    assert export_to_csv([{"event_type": "agent.end"}, {}], out) == 2

    first, second = _read_csv(out)
    assert first["event_type"] == "agent.end"
    assert first["tool_name"] == first["confidence"] == first["status"] == ""
    assert first["session_id"] == ""
    assert json.loads(first["metadata_json"]) == {}
    assert all(second[column] == "" for column in CSV_COLUMNS if column != "metadata_json")


def test_csv_handles_null_sections_and_wrong_types(tmp_path: Path) -> None:
    out = tmp_path / "odd.csv"
    export_to_csv(
        [
            {"event_type": "x", "tool_call": None, "confidence": None},
            {"event_type": "y", "tool_call": "oops", "confidence": "high"},
            {"event_type": "z", "confidence": True},
            {"event_type": "w", "confidence": 1},
        ],
        out,
    )
    rows = _read_csv(out)
    assert [row["tool_name"] for row in rows] == ["", "", "", ""]
    assert [row["confidence"] for row in rows] == ["", "", "", "1.0"]


def test_csv_uses_the_tool_result_name_when_there_is_no_tool_call(tmp_path: Path) -> None:
    out = tmp_path / "tr.csv"
    export_to_csv([{"event_type": "tool.result", "tool_result": {"tool_name": "grep"}}], out)
    (row,) = _read_csv(out)
    assert row["tool_name"] == "grep"


def test_csv_session_id_falls_back_to_the_argument(tmp_path: Path) -> None:
    out = tmp_path / "sid.csv"
    export_to_csv([{"event_type": "a"}, {"event_type": "b", "session_id": "own"}], out, "fallback")
    rows = _read_csv(out)
    assert [row["session_id"] for row in rows] == ["fallback", "own"]


def test_csv_quotes_commas_quotes_and_newlines_and_round_trips(tmp_path: Path) -> None:
    out = tmp_path / "rt.csv"
    event = {
        "event_type": "tool.call",
        "tool_call": {"tool_name": 'say "hi", then\nbye', "raw_command": "a,b\nc"},
    }
    export_to_csv([event], out)

    (row,) = _read_csv(out)
    assert row["tool_name"] == 'say "hi", then\nbye'
    assert json.loads(row["metadata_json"])["tool_call"] == event["tool_call"]


def test_csv_round_trips_unicode(tmp_path: Path) -> None:
    out = tmp_path / "uni.csv"
    export_to_csv([{"event_type": "x", "tool_call": {"tool_name": "résumé ✓ 日本"}}], out)
    (row,) = _read_csv(out)
    assert row["tool_name"] == "résumé ✓ 日本"


def test_csv_empty_input_writes_only_the_header(tmp_path: Path) -> None:
    out = tmp_path / "none.csv"
    assert export_to_csv([], out) == 0
    assert out.read_text(encoding="utf-8").strip() == ",".join(CSV_COLUMNS)


def test_csv_uses_unix_line_endings(tmp_path: Path) -> None:
    out = tmp_path / "eol.csv"
    export_to_csv([FULL_EVENT], out)
    assert b"\r\n" not in out.read_bytes()


@pytest.mark.parametrize("payload", ["=1+1", "+SUM(A1)", "-2+3", "@cmd", "\tcmd", "\rcmd"])
def test_csv_neutralizes_spreadsheet_formulas(tmp_path: Path, payload: str) -> None:
    out = tmp_path / "f.csv"
    export_to_csv(
        [{"event_type": "x", "status": payload, "tool_call": {"tool_name": payload}}], out
    )
    (row,) = _read_csv(out)
    assert row["tool_name"] == "'" + payload
    assert row["status"] == "'" + payload


def test_csv_leaves_normal_text_alone(tmp_path: Path) -> None:
    row = flatten_event({"event_type": "tool.call", "status": "success"})
    assert row["event_type"] == "tool.call"
    assert row["status"] == "success"


def test_csv_metadata_json_is_stable_between_runs(tmp_path: Path) -> None:
    a = flatten_event({"event_type": "x", "b": 1, "a": 2})
    b = flatten_event({"a": 2, "event_type": "x", "b": 1})
    assert a == b


def test_csv_handles_real_agent_event_dumps(tmp_path: Path) -> None:
    event = AgentEvent(
        session_id="real",
        agent_id="agent-1",
        event_type=EventType.TOOL_CALL,
        status=ExecutionStatus.SUCCESS,
        confidence=ConfidenceData(overall_score=0.9),
    )
    out = tmp_path / "real.csv"

    assert export_to_csv([event.model_dump(mode="json")], out) == 1

    (row,) = _read_csv(out)
    assert row["event_type"] == "tool.call"
    assert row["status"] == "success"
    assert row["session_id"] == "real"
    assert row["confidence"] == "0.9"
    assert row["timestamp"]


def test_csv_handles_raw_model_dump_with_enums_and_datetimes(tmp_path: Path) -> None:
    event = AgentEvent(
        session_id="py",
        agent_id="agent-1",
        event_type=EventType.TOOL_CALL,
        status=ExecutionStatus.SUCCESS,
    )
    out = tmp_path / "py.csv"
    export_to_csv([event.model_dump()], out)
    (row,) = _read_csv(out)
    assert row["event_type"] == "tool.call"
    assert row["status"] == "success"
    assert row["timestamp"].startswith("20")


def test_export_raises_when_the_directory_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        export_to_jsonl([FULL_EVENT], tmp_path / "missing" / "x.jsonl")
    with pytest.raises(OSError):
        export_to_csv([FULL_EVENT], tmp_path / "missing" / "x.csv")
