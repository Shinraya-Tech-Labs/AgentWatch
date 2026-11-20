"""Interactive human-in-the-loop approval gate."""

from __future__ import annotations

import sys
import time
from typing import Any


class HumanApprovalGate:
    def __init__(self, auto_approve: bool = False) -> None:
        self.auto_approve = auto_approve

    def request_approval(
        self, tool_name: str, arguments: dict[str, Any], timeout_seconds: float = 30.0
    ) -> bool:
        if self.auto_approve:
            return True

        args_repr = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        print(
            f"⚠️  [Panopti HITL] Agent requested tool call: {tool_name}({args_repr})",
            flush=True,
        )
        print(
            f"Approve execution? [y/N] (Auto-denies in {timeout_seconds:g}s): ",
            end="",
            flush=True,
        )

        try:
            import select

            ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
            if not ready:
                print()
                return False
            answer = sys.stdin.readline().strip().lower()
        except (OSError, ValueError):
            # Fallback for environments without select on stdin (e.g. Windows pipes)
            deadline = time.monotonic() + timeout_seconds
            # Best-effort; blocking input fallback
            try:
                answer = input().strip().lower()
            except EOFError:
                return False
            if time.monotonic() > deadline:
                return False

        return answer == "y"