"""CMP-003/004 — PII/PHI redaction for telemetry (issue #398)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from panoptis.api.server import app
from panoptis.core.schema import ToolCallData
from panoptis.core.watcher import GenericAdapter, watch
from panoptis.security.redaction import (
    MASK,
    Redactor,
    redact,
    redact_payload,
    redact_tool_call,
)

# Force the deterministic regex backend so tests don't depend on Presidio.
_R = Redactor(use_presidio=False)


def test_regex_backend_selected_without_presidio():
    assert _R.backend == "regex"


def test_pii_is_masked():
    out = _R.redact_text("contact bob@acme.com, SSN 123-45-6789, ph 555-123-4567")
    assert "bob@acme.com" not in out
    assert "123-45-6789" not in out
    assert MASK in out


def test_phi_is_masked():
    out = _R.redact_text("Patient MRN: A12345 diagnosis: diabetes, ICD-10 E11.9")
    assert "A12345" not in out
    assert "diabetes" not in out
    assert MASK in out


def test_redact_payload_recurses():
    payload = {
        "cmd": "email alice@corp.com",
        "nested": ["dx: cancer stage 2", {"ssn": "123-45-6789"}],
        "count": 3,
    }
    out = _R.redact_payload(payload)
    assert "alice@corp.com" not in str(out)
    assert "123-45-6789" not in str(out)
    assert out["count"] == 3  # non-strings untouched


def test_empty_and_none_passthrough():
    assert _R.redact_text("") == ""
    assert _R.redact_text(None) is None


def test_redact_tool_call_scrubs_command_and_args():
    tc = ToolCallData(
        tool_name="bash",
        raw_command="curl https://api/send?ssn=123-45-6789",
        arguments={"email": "bob@acme.com", "ok": True},
    )
    red = redact_tool_call(tc)
    assert "123-45-6789" not in (red.raw_command or "")
    assert "bob@acme.com" not in str(red.arguments)
    assert red.arguments["ok"] is True


def test_module_level_helpers():
    assert MASK in redact("ssn 123-45-6789")
    # A value that is entirely PII collapses to the mask.
    assert redact_payload({"x": "bob@acme.com"})["x"] == MASK


# ── watcher integration ───────────────────────────────────────────────────


class _DummyAgent:
    def run(self, *a, **k):
        return "ok"


def test_watcher_maybe_redact_off_by_default():
    adapter = GenericAdapter(_DummyAgent())
    tc = ToolCallData(tool_name="bash", raw_command="ssn 123-45-6789")
    assert adapter._maybe_redact(tc).raw_command == "ssn 123-45-6789"


def test_watcher_maybe_redact_scrubs_when_enabled():
    adapter = GenericAdapter(_DummyAgent(), redact=True)
    tc = ToolCallData(tool_name="bash", raw_command="ssn 123-45-6789")
    assert "123-45-6789" not in adapter._maybe_redact(tc).raw_command


def test_safety_checks_raw_then_publishes_redacted_event():
    """The safety engine must evaluate the raw payload; only the published /
    persisted event is scrubbed (redaction must not run before the check)."""
    from panoptis.core.event_bus import EventBus
    from panoptis.core.schema import EventType

    seen: dict[str, str] = {}

    class _RecordingSafety:
        def check_tool_call_sync(self, tool_call):
            seen["raw_command"] = tool_call.raw_command
            return False, []

    class _Agent:
        def run(self, command):
            return "ok"

    bus = EventBus()
    agent = _Agent()
    GenericAdapter(agent, event_bus=bus, safety_engine=_RecordingSafety(), redact=True).attach()
    agent.run("ssn 123-45-6789")

    # Safety saw the unredacted command.
    assert seen["raw_command"] == "ssn 123-45-6789"

    # The published TOOL_CALL event is scrubbed.
    events = bus.get_recent_events(event_type=EventType.TOOL_CALL)
    assert events
    published = events[0].tool_call.raw_command
    assert "123-45-6789" not in published
    assert MASK in published


def test_watch_passes_redact_through():
    from panoptis.core.event_bus import EventBus
    from panoptis.core.schema import EventType

    class _Agent:
        def run(self, command):
            return "ok"

    bus = EventBus()
    agent = watch(_Agent(), event_bus=bus, redact=True)
    agent.run("ssn 123-45-6789")

    events = bus.get_recent_events(event_type=EventType.TOOL_CALL)
    assert events
    assert MASK in events[0].tool_call.raw_command


# ── EU AI Act Article 15 export endpoint ──────────────────────────────────


def test_eu_ai_act_report_endpoint(monkeypatch):
    # Pin auth off so the test doesn't depend on a stray AGENTWATCH_API_KEY.
    monkeypatch.setattr("panoptis.api.server._API_KEY", None)
    client = TestClient(app)
    resp = client.get("/api/v1/governance/eu-ai-act-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["article"] == "EU AI Act Article 15"
    assert "conformity" in body
    assert "requirements_met" in body["conformity"]
    # Fields are derived from live telemetry/policy, not static literals.
    assert "telemetry" in body
    assert "safety_stats" in body["telemetry"]
    assert "accuracy_metrics" in body["documentation"]
    assert "active_policy" in body["documentation"]["data_governance"]
    assert body["documentation"]["risk_category"] == "high"
