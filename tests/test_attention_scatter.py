from __future__ import annotations

from datetime import UTC, datetime

from panoptis.core.schema import AgentEvent, EventType, ToolCallData
from panoptis.lattice.attention_scatter import (
    AttentionScatterDetector,
    _domain_from_url,
    _extract_parent_dir,
    _safe_entropy,
    compute_scatter_score,
)


def _event(
    step: int,
    affected_resources: list[str] | None = None,
    tool_name: str = "read_file",
) -> AgentEvent:
    return AgentEvent(
        event_type=EventType.TOOL_CALL,
        session_id="s1",
        agent_id="a1",
        step_number=step,
        timestamp=datetime.now(UTC),
        tool_call=ToolCallData(
            tool_name=tool_name,
            affected_resources=affected_resources or [],
        ),
    )


# ----------------------------------------------------------------- entropy helper


def test_entropy_empty():
    assert _safe_entropy([]) == 0.0


def test_entropy_single():
    assert _safe_entropy(["a"]) == 0.0


def test_entropy_uniform():
    assert _safe_entropy(["a", "b", "c"]) > 0.95


def test_entropy_skewed():
    assert _safe_entropy(["a", "a", "a", "b"]) < 0.9


# ----------------------------------------------------------------- parent dir


def test_parent_dir_deep():
    assert _extract_parent_dir("/workspace/src/module/file.py") == "/workspace/src/module"


def test_parent_dir_shallow():
    assert _extract_parent_dir("/workspace/readme.md") == "/workspace"


# ----------------------------------------------------------------- domain


def test_domain_from_https():
    assert _domain_from_url("https://api.example.com/v1/data") == "api.example.com"


def test_domain_from_http():
    assert _domain_from_url("http://localhost:8080/status") == "localhost:8080"


def test_domain_unparseable():
    assert _domain_from_url("not-a-url") == "not-a-url"


# ----------------------------------------------------------------- scorer


def test_scorer_zero():
    assert compute_scatter_score(0.0, 0.0) == 0.0


def test_scorer_path_dominates():
    assert compute_scatter_score(0.9, 0.2) == 0.9


def test_scorer_domain_dominates():
    assert compute_scatter_score(0.1, 0.85) == 0.85


# ----------------------------------------------------------------- detector — non-tool events pass through safely


def test_non_tool_event_ignored():
    det = AttentionScatterDetector()
    result = det.observe(
        AgentEvent(
            event_type=EventType.SESSION_START,
            session_id="s1",
            agent_id="a1",
            timestamp=datetime.now(UTC),
        )
    )
    assert result.blocked is False
    assert result.score == 0.0
    assert result.explanation == ""


# ----------------------------------------------------------------- focused access (same directory, same domain)


def test_focused_path_access_is_safe():
    det = AttentionScatterDetector(window_size=10, scatter_threshold=0.70)
    for step in range(5):
        result = det.observe(_event(step, ["/workspace/src/utils.py"]))
        assert result.blocked is False, f"step {step}"
        assert result.score == 0.0, f"step {step}: score={result.score}"


def test_focused_domain_access_is_safe():
    det = AttentionScatterDetector(window_size=10, scatter_threshold=0.70)
    for step in range(4):
        result = det.observe(_event(step, ["https://api.openai.com/v1/chat/completions"]))
        assert result.blocked is False, f"step {step}"


# ----------------------------------------------------------------- moderate scatter (warn, not block)


def test_modest_scatter_warns():
    # Use threshold just above 1.0 so even a 3-distinct-paths window won't block.
    det = AttentionScatterDetector(window_size=10, scatter_threshold=1.5)
    # 3 distinct paths across 3 dirs → entropy 1.0 (max possible for N=3) — must NOT block
    det.observe(_event(1, ["/workspace/src/main.py"]))
    det.observe(_event(2, ["/workspace/tests/test.py"]))
    result = det.observe(_event(3, ["/workspace/docs/readme.md"]))
    assert result.blocked is False, f"expected no block at threshold 1.5, got {result.explanation}"
    assert result.path_count == 3
    assert result.path_entropy > 0.0


# ----------------------------------------------------------------- scatter BLOCKED (many distinct directories)


def test_wide_scatter_is_blocked():
    det = AttentionScatterDetector(window_size=10, scatter_threshold=0.50)
    dirs = [
        "/etc/passwd",
        "/home/user/.bashrc",
        "/var/log/syslog",
        "/tmp/session",  # noqa: S108 (test fixture, not a real path)
        "/boot/efi",
        "/opt/tool",
        "/srv/backup",
    ]
    for i, path in enumerate(dirs):
        result = det.observe(_event(i, [path]))
    assert result.blocked is True
    assert "BLOCKED" in result.explanation
    assert result.score > 0.50


# ----------------------------------------------------------------- many domains = blocked


def test_many_domains_blocked():
    det = AttentionScatterDetector(window_size=8, scatter_threshold=0.50)
    domains = [
        "https://api.openai.com/chat",
        "https://api.anthropic.com/messages",
        "https://api.google.com/ai",
        "https://api.mistral.ai/chat",
        "https://api.cohere.ai/chat",
        "https://api.groq.com/chat",
    ]
    for i, domain in enumerate(domains):
        result = det.observe(_event(i, [domain]))
    assert result.blocked is True
    assert "BLOCKED" in result.explanation


# ----------------------------------------------------------------- reset


def test_reset_clears_state():
    det = AttentionScatterDetector(window_size=5, scatter_threshold=0.50)
    for step in range(5):
        det.observe(_event(step, [f"/workspace/module_{step}/file.py"]))
    assert (
        det.observe(
            _event(5, ["/etc/passwd"]),
        ).blocked
        is True
    )
    det.reset()
    assert (
        det.observe(
            _event(6, ["/workspace/src/main.py"]),
        ).blocked
        is False
    )


# ----------------------------------------------------------------- window overflow


def test_window_overflow_drops_old_entries():
    det = AttentionScatterDetector(window_size=3, scatter_threshold=0.50)
    det.observe(_event(1, ["/a/x.txt"]))
    det.observe(_event(2, ["/b/y.txt"]))
    det.observe(_event(3, ["/c/z.txt"]))
    # window now contains 3 paths across 3 dirs → scatter
    result = det.observe(_event(4, ["/a/q.txt"]))
    # old "/a/x.txt" dropped out, "/c/z.txt" still present → should be blocked
    assert result.blocked
    # next 3 steps stabilize
    det.observe(_event(5, ["/a/p.txt"]))
    det.observe(_event(6, ["/a/o.txt"]))
    result = det.observe(_event(7, ["/a/n.txt"]))
    assert result.blocked is False, (
        f"expected safety after saturation, got blocked score={result.score}"
    )


# ----------------------------------------------------------------- scored quantiles


def test_score_0_for_identical_items():
    det = AttentionScatterDetector(window_size=4)
    for i in range(4):
        result = det.observe(_event(i, ["/src/main.py"]))
    assert result.score == 0.0


def test_score_increases_with_disparity():
    det = AttentionScatterDetector(window_size=4)
    det.observe(_event(1, ["/src/main.py"]))
    result2 = det.observe(_event(2, ["/src/utils.py"]))
    assert result2.score == 0.0
    result3 = det.observe(_event(3, ["/tests/test.py"]))
    assert result3.score > 0.0, result3.explanation
    result4 = det.observe(_event(4, ["/docs/readme.md"]))
    assert result4.score > result3.score


# ----------------------------------------------------------------- mixed resources


def test_paths_and_domains_mixed():
    det = AttentionScatterDetector(window_size=4, scatter_threshold=0.95)
    det.observe(_event(1, ["https://api.openai.com/chat", "/workspace/src/main.py"]))
    det.observe(_event(2, ["https://api.anthropic.com/messages", "/workspace/tests/test.py"]))
    result = det.observe(_event(3, ["https://api.google.com/ai", "/workspace/docs/readme.md"]))
    # With high threshold 0.95 and 6 distinct items, scatter IS at 1.0 → blocked
    assert result.blocked is True, f"expected BLOCKED with high scatter, got {result.explanation}"
    assert result.path_count == 3
    assert result.domain_count == 3


# ----------------------------------------------------------------- reset on create


def test_fresh_detector_has_zero_entropy():
    det = AttentionScatterDetector()
    result = det.observe(_event(1, ["/src/main.py"]))
    assert result.score == 0.0
    assert result.blocked is False
