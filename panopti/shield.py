"""Anti-tampering process and audit shield for Panopti & AgentWatch."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any


class TamperAttemptBlocked(PermissionError):  # noqa: N818
    """Raised when an agent attempts to kill guardians or destroy audit logs."""


class AntiTamperShield:
    """Intersects and blocks destructive commands targeting guardians and audit trails."""

    PROCESS_PATTERNS = (
        r"\b(?:pkill|killall|taskkill)\b.*?\b(?:panopti|agentwatch)\b",
        r"\bkill\b.*?\bpgrep\b.*?\b(?:panopti|agentwatch)\b",
    )
    FILE_PATTERNS = (
        r"\b(?:rm|unlink|shred|truncate|del|erase)\b.*?\b(?:panopti\.db|agentwatch(?:\.jsonl|\.db)|panopti\.log)\b",
        r">\s*.*?\b(?:panopti\.db|agentwatch(?:\.jsonl|\.db)|panopti\.log)\b",
    )

    def __init__(self, target_pids: set[int] | None = None) -> None:
        self.target_pids = set(target_pids) if target_pids is not None else {os.getpid()}

    def is_tampering_attempt(self, command: str) -> bool:
        if not command or not command.strip():
            return False
        cmd = command.strip()
        for pat in self.PROCESS_PATTERNS + self.FILE_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                return True
        for pid in self.target_pids:
            if re.search(rf"\b(?:kill|taskkill)\b.*?\b{pid}\b", cmd, re.IGNORECASE):
                return True
        return False

    def verify(self, command: str) -> None:
        if self.is_tampering_attempt(command):
            raise TamperAttemptBlocked(f"Anti-tamper shield vetoed command: {command}")

    def guard(self, func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(command: str, *args: Any, **kwargs: Any) -> Any:
            self.verify(command)
            return func(command, *args, **kwargs)

        return wrapper
