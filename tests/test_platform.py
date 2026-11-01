"""Phase 9 — Platform tests (PLT-001..009)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from panoptis.core.schema import (
    AgentEvent,
    AgentSession,
    EventType,
    ExecutionStatus,
    PluginManifest,
    ToolCallData,
    ToolResultData,
)
from panoptis.platform.cloud import TenantStore
from panoptis.platform.eval_builder import EvalDataset, EvalLabel
from panoptis.platform.intelligence import AgentWatchIntelligence
from panoptis.platform.prompts import PromptRegistry
from panoptis.platform.sharing import (
    ShareLinkRegistry,
    ShareScope,
    render_for_viewer,
    sanitize_trace,
)
from panoptis.plugins.registry import PluginRegistry

# ─────────────────────────────────────────────
# PLT-001 — Session sharing
# ─────────────────────────────────────────────


def test_share_link_create_and_resolve():
    reg = ShareLinkRegistry()
    link = reg.create("session-1", scope=ShareScope.REDACTED, ttl=timedelta(hours=1))
    resolved = reg.resolve(link.token)
    assert resolved is not None
    assert resolved.session_id == "session-1"
    assert link.url_path.endswith(link.token)


def test_share_link_revoked():
    reg = ShareLinkRegistry()
    link = reg.create("session-1")
    reg.revoke(link.token)
    assert reg.resolve(link.token) is None


def test_share_link_expired():
    reg = ShareLinkRegistry()
    link = reg.create("session-1", ttl=timedelta(seconds=-1))
    assert reg.resolve(link.token) is None


def test_render_for_viewer_redacts():
    payload = {
        "session_id": "S",
        "events": [
            {
                "tool_call": {
                    "tool_name": "bash",
                    "raw_command": "secret command",
                    "arguments": {"command": "rm -rf /"},
                }
            }
        ],
        "api_keys": ["sk-abc"],
    }
    out = render_for_viewer(payload, ShareScope.REDACTED)
    assert out["api_keys"] == "[REDACTED]"
    assert out["events"][0]["tool_call"]["raw_command"] == "[REDACTED]"


def test_sanitize_trace_strips_secret_keys_at_any_depth():
    payload = {
        "session": {"credentials": {"password": "hunter2"}},
        "steps": [{"env": {"OPENAI_API_KEY": "sk-live-xyz"}}],
        "api_keys": ["sk-abc"],
    }
    out = sanitize_trace(payload)
    assert out["api_keys"] == "[REDACTED]"
    assert out["session"]["credentials"] == "[REDACTED]"
    assert out["steps"][0]["env"] == "[REDACTED]"


def test_sanitize_trace_masks_free_text_pii():
    payload = {
        "events": [{"tool_call": {"raw_command": "mail bob@acme.com about SSN 123-45-6789"}}]
    }
    cmd = sanitize_trace(payload)["events"][0]["tool_call"]["raw_command"]
    assert "bob@acme.com" not in cmd
    assert "123-45-6789" not in cmd
    assert "[REDACTED]" in cmd


def test_sanitize_trace_preserves_benign_content():
    payload = {"session": {"session_id": "abc-123", "status": "success"}}
    out = sanitize_trace(payload)
    assert out["session"]["session_id"] == "abc-123"
    assert out["session"]["status"] == "success"


# ─────────────────────────────────────────────
# PLT-005 — Prompt versioning
# ─────────────────────────────────────────────


def test_prompt_registry_versioning_and_rollback():
    reg = PromptRegistry()
    v1 = reg.register("greet", "Hi.")
    v2 = reg.register("greet", "Hello, friend.")
    assert reg.active("greet") == v2
    prev = reg.rollback("greet")
    assert prev == v1
    assert reg.active("greet") == v1


def test_prompt_registry_ab_picks_a_version():
    reg = PromptRegistry()
    v1 = reg.register("p", "alpha")
    v2 = reg.register("p", "beta")
    reg.set_ab_split("p", {v1.version: 0.5, v2.version: 0.5})
    chosen = reg.select("p")
    assert chosen is not None
    assert chosen.version in (v1.version, v2.version)


def test_prompt_auto_rollback_on_low_confidence():
    reg = PromptRegistry()
    v1 = reg.register("p", "old")
    v2 = reg.register("p", "new")
    for _ in range(10):
        reg.record_outcome("p", v2.version, confidence=0.3, success=False)
    rolled = reg.auto_rollback_on_drop("p", min_confidence=0.5)
    assert rolled == v1


# ─────────────────────────────────────────────
# PLT-006 — Eval dataset builder
# ─────────────────────────────────────────────


def test_eval_dataset_add_and_filter():
    ds = EvalDataset("regression")
    ds.add("s1", "summarize doc", "summary returned", EvalLabel.GOLDEN)
    ds.add("s2", "summarize doc", "hallucinated", EvalLabel.FAILURE)
    assert len(ds) == 2
    assert len(ds.filter(EvalLabel.GOLDEN)) == 1
    jsonl = ds.to_jsonl()
    assert "golden" in jsonl and "failure" in jsonl


# ─────────────────────────────────────────────
# PLT-007 — Tenant store
# ─────────────────────────────────────────────


def test_tenant_store_org_team_membership():
    store = TenantStore()
    org = store.create_org("Acme", plan="team")
    team = store.create_team(org.org_id, "platform")
    store.add_member(team.team_id, "user-1")
    teams = store.teams_for("user-1")
    assert any(t.team_id == team.team_id for t in teams)


def test_tenant_store_usage_summary():
    store = TenantStore()
    org = store.create_org("Acme")
    team = store.create_team(org.org_id, "ml")
    for _ in range(3):
        store.record_usage(team.team_id, "sessions", 1)
    store.record_usage(team.team_id, "usd", 12.5)
    summary = store.summary(team.team_id)
    assert summary.sessions == 3
    assert summary.usd == 12.5


# ─────────────────────────────────────────────
# PLT-008 — Plugin registry
# ─────────────────────────────────────────────


def test_plugin_registry_install_and_uninstall():
    reg = PluginRegistry()
    manifest = PluginManifest(
        plugin_id="p1",
        name="example",
        version="0.1.0",
        author="dev",
        description="x",
        trust_level=2,
    )
    record = reg.install(manifest)
    assert reg.get("p1") is record
    reg.disable("p1")
    assert not reg.get("p1").enabled
    assert reg.uninstall("p1")
    assert reg.get("p1") is None


def test_plugin_registry_checksum_mismatch_raises():
    import pytest

    payload = b"hello"
    bad_checksum = "0" * 64
    manifest = PluginManifest(
        plugin_id="bad",
        name="bad",
        version="0.1.0",
        author="dev",
        description="x",
        checksum_sha256=bad_checksum,
    )
    reg = PluginRegistry()
    with pytest.raises(ValueError):
        reg.install(manifest, payload=payload)


# ─────────────────────────────────────────────
# PLT-009 — Intelligence
# ─────────────────────────────────────────────


def test_intelligence_handles_empty():
    rep = AgentWatchIntelligence().analyze([])
    assert any(i.title == "No telemetry yet" for i in rep.insights)


def test_intelligence_flags_failing_tool():
    sessions = []
    now = datetime.now(UTC)
    for i in range(10):
        sess = AgentSession(
            session_id=f"s{i}",
            agent_id="A",
            started_at=now,
            ended_at=now + timedelta(seconds=1),
            status=ExecutionStatus.SUCCESS,
        )
        # 6 of 10 fails
        evs = [
            AgentEvent(
                session_id=sess.session_id,
                agent_id="A",
                event_type=EventType.TOOL_CALL,
                tool_call=ToolCallData(tool_name="flaky"),
            ),
            AgentEvent(
                session_id=sess.session_id,
                agent_id="A",
                event_type=EventType.TOOL_ERROR if i < 6 else EventType.TOOL_RESULT,
                tool_result=ToolResultData(tool_name="flaky"),
                status=ExecutionStatus.FAILURE if i < 6 else ExecutionStatus.SUCCESS,
            ),
        ]
        sessions.append((sess, evs))
    rep = AgentWatchIntelligence().analyze(sessions)
    assert any("flaky" in i.title for i in rep.insights)
