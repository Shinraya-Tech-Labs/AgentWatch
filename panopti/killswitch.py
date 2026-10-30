"""Emergency Kill Switch: POSIX signal & file-based sentinel runtime tripwire."""

from __future__ import annotations

import functools
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class EmergencyStopTriggered(Exception):  # noqa: N818
    """Raised when an operation is attempted while emergency kill switch is tripped."""

    def __init__(self, reason: str = "Emergency stop active", ts: float | None = None) -> None:
        self.reason, self.timestamp = reason, ts or time.time()
        super().__init__(f"[{self.timestamp}] Emergency stop tripped: {self.reason}")


class EmergencyKillSwitch:
    """Thread-safe file-sentinel and POSIX signal emergency kill switch."""

    def __init__(self, kill_file: str | Path | None = None, enable_signals: bool = True) -> None:
        self._lock = threading.RLock()
        self._tripped, self.reason, self.tripped_at = False, "Manual operator trip", None
        if kill_file is None:
            a = sys.argv
            i = a.index("--kill-file") if "--kill-file" in a else -1
            kill_file = (
                a[i + 1] if 0 <= i < len(a) - 1 else os.getenv("PANOPTI_KILL_FILE", ".panopti.kill")
            )
        self.kill_file = Path(kill_file)
        if enable_signals and hasattr(signal, "SIGUSR1"):
            try:
                signal.signal(signal.SIGUSR1, self.handle_signal)
            except (ValueError, OSError):
                pass

    def handle_signal(self, signum: int | None = None, frame: Any = None) -> None:
        try:
            name = signal.Signals(signum).name if signum is not None else "SIGUSR1"
        except (ValueError, AttributeError):
            name = str(signum or "SIGUSR1")
        sig_str = name if str(signum) in name else f"{name} ({signum})"
        self.trip(f"POSIX signal {sig_str} received")

    def trip(self, reason: str = "Manual operator trip") -> None:
        with self._lock:
            self._tripped, self.reason, self.tripped_at = True, reason, time.time()

    def reset(self) -> None:
        with self._lock:
            self._tripped, self.reason, self.tripped_at = False, "Manual operator trip", None
            try:
                self.kill_file.unlink(missing_ok=True)
            except OSError:
                pass

    def is_tripped(self) -> bool:
        with self._lock:
            if not self._tripped and self.kill_file.exists():
                try:
                    txt = self.kill_file.read_text(encoding="utf-8").strip()
                except OSError:
                    txt = ""
                self.trip(txt or f"Kill file sentinel detected: {self.kill_file}")
            return self._tripped

    def verify(self) -> None:
        if self.is_tripped():
            raise EmergencyStopTriggered(self.reason, self.tripped_at)

    check = verify

    def guard(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.verify()
            return fn(*args, **kwargs)

        return wrapper
