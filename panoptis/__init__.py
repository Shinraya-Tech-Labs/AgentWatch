"""AgentWatch — Reliability, Safety, and Observability for AI Agents."""

from panoptis._version import __version__
from panoptis.core.watcher import (
    AgentWatchBlockedError,
    GenericAdapter,
    detect_framework,
    detect_framework_label,
    watch,
)

__all__ = [
    "__version__",
    "watch",
    "detect_framework",
    "detect_framework_label",
    "GenericAdapter",
    "AgentWatchBlockedError",
]
