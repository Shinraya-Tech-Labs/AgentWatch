"""Verifies that DSL rules are enforced the same in sync and async contexts."""

import asyncio

from panoptis.core.policy_dsl import PolicyEngine
from panoptis.core.safety import SafetyEngine
from panoptis.core.schema import AgentEvent, AgentFramework, EventType, ToolCallData


def test_sync_vs_async_parity():
    """
    Verifies parity between synchronous and asynchronous execution.
    """
    dsl = """
    rules:
      - if: command contains "secret"
        then: block
    """
    policy_engine = PolicyEngine.from_yaml(dsl)
    engine = SafetyEngine(policy_engine=policy_engine)

    # A command that matches the DSL rule but NOT the builtin patterns
    tool_call = ToolCallData(tool_name="bash", raw_command="cat secret.txt")

    event = AgentEvent(
        session_id="test-session",
        agent_id="test-agent",
        framework=AgentFramework.CLAUDE_CODE,
        event_type=EventType.TOOL_CALL,
        tool_call=tool_call,
    )

    # 1. Test Sync
    blocked_sync, reasons_sync = engine.check_tool_call_sync(tool_call)

    # 2. Test Async
    try:
        old_loop = asyncio.get_event_loop_policy().get_event_loop()
    except (RuntimeError, AssertionError):
        old_loop = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        checked_event = loop.run_until_complete(engine.check_event(event))
        assert checked_event.safety is not None
        blocked_async = checked_event.safety.blocked
        reasons_async = checked_event.safety.reasons
    finally:
        loop.close()
        if old_loop:
            asyncio.set_event_loop(old_loop)
        else:
            asyncio.set_event_loop(None)

    print("\n[PARITY CHECK - DSL BYPASS]")
    print(f"Command: {tool_call.raw_command}")
    print(f"Sync:  blocked={blocked_sync}, reasons={reasons_sync}")
    print(f"Async: blocked={blocked_async}, reasons={reasons_async}")

    # Assertion parity of block state and reasons
    assert blocked_sync == blocked_async, (
        f"Sync/async block discrepancy: {blocked_sync} != {blocked_async}"
    )
    assert reasons_sync == reasons_async, (
        f"Sync/async reasons discrepancy: {reasons_sync} != {reasons_async}"
    )
    assert (len(reasons_sync) > 0) == blocked_sync, (
        "Metadata discrepancy: blocked is True but no reasons provided"
    )


def test_tool_parameter_scope_constraints():
    """
    Test feature [ELUSOC] from Issue #364:
    Ensures policy DSL blocks tool calls if input arguments violate threshold parameters.
    """
    # 1. Define a YAML rule that restricts high-value transfers
    dsl = """
    rules:
      - if: tool == "request_funds" and args.amount > 500
        then: block
        label: "high_value_funds_restriction"
    """
    policy_engine = PolicyEngine.from_yaml(dsl)
    safety_engine = SafetyEngine(policy_engine=policy_engine)

    # 2. Test within safe parameters (amount = 250, allowed)
    allowed_tool_call = ToolCallData(
        tool_name="request_funds",
        raw_command="",
        arguments={"amount": 250},  # Natively parsed by policy_dsl via args.amount
    )
    blocked_allow, reasons_allow = safety_engine.check_tool_call_sync(allowed_tool_call)

    assert blocked_allow is False
    assert len(reasons_allow) == 0

    # 3. Test outside safe parameters (amount = 600, blocked)
    blocked_tool_call = ToolCallData(
        tool_name="request_funds", raw_command="", arguments={"amount": 600}
    )
    blocked_deny, reasons_deny = safety_engine.check_tool_call_sync(blocked_tool_call)

    assert blocked_deny is True
    assert "rule_matched:high_value_funds_restriction" in reasons_deny


if __name__ == "__main__":
    test_sync_vs_async_parity()
    test_tool_parameter_scope_constraints()
