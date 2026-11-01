"""
Agent Execution Logging

Comprehensive structured logging for agent execution lifecycle, including
parameter tracking, API calls, responses, and error context.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionLogger:
    """Structured logging for agent task execution."""

    def __init__(self, agent_id: str, session_id: str, task_id: str) -> None:
        """Initialize execution logger with context."""
        self.agent_id = agent_id
        self.session_id = session_id
        self.task_id = task_id
        self.context = {
            "agent_id": agent_id,
            "session_id": session_id,
            "task_id": task_id,
            "started_at": datetime.now(UTC).isoformat(),
        }

    def _format_log(self, level: str, message: str, **kwargs: Any) -> dict[str, Any]:
        """Format log entry with context and metadata."""
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            **self.context,
            **kwargs,
        }

    def log_step(self, step_name: str, details: dict[str, Any] | None = None) -> None:
        """Log an execution step."""
        logger.info(
            json.dumps(
                self._format_log(
                    "INFO",
                    f"Execution step: {step_name}",
                    step=step_name,
                    details=details or {},
                )
            )
        )

    def log_api_call(
        self,
        endpoint: str,
        method: str,
        parameters: dict[str, Any],
        headers: dict[str, Any],
    ) -> None:
        """Log API call with parameters."""
        logger.info(
            json.dumps(
                self._format_log(
                    "INFO",
                    f"API call: {method} {endpoint}",
                    api_endpoint=endpoint,
                    api_method=method,
                    api_parameters=self._redact_sensitive(parameters),
                    api_headers=self._redact_sensitive(headers),
                )
            )
        )

    def log_api_response(
        self,
        endpoint: str,
        status_code: int,
        response_body: Any,
        latency_ms: float,
    ) -> None:
        """Log API response with latency."""
        logger.info(
            json.dumps(
                self._format_log(
                    "INFO",
                    f"API response: {status_code}",
                    api_endpoint=endpoint,
                    api_status=status_code,
                    api_response=self._redact_sensitive(response_body),
                    latency_ms=latency_ms,
                )
            )
        )

    def log_error(
        self,
        error_message: str,
        error_type: str,
        stack_trace: str,
        context_data: dict[str, Any] | None = None,
    ) -> None:
        """Log error with full context and stack trace."""
        logger.error(
            json.dumps(
                self._format_log(
                    "ERROR",
                    error_message,
                    error_type=error_type,
                    error_stack_trace=stack_trace,
                    error_context=context_data or {},
                )
            )
        )

    def log_execution_complete(
        self,
        status: str,
        duration_ms: float,
        result: Any | None = None,
    ) -> None:
        """Log execution completion."""
        logger.info(
            json.dumps(
                self._format_log(
                    "INFO",
                    f"Execution complete: {status}",
                    execution_status=status,
                    duration_ms=duration_ms,
                    result=self._redact_sensitive(result),
                )
            )
        )

    _SENSITIVE_SUBSTRINGS: frozenset[str] = frozenset(
        {"authorization", "x-api-key", "api_key", "token", "password", "secret"}
    )

    @classmethod
    def _redact_sensitive(cls, data: Any) -> Any:
        """Recursively redact sensitive keys from dicts and lists.

        Matching is case-insensitive and covers common variations such as
        Authorization, X-API-Key, api_key, token, password, and secret.
        Nested dicts and lists are traversed; all other values are returned
        unchanged. The original structure is never mutated.
        """
        if isinstance(data, dict):
            result: dict[str, Any] = {}
            for k, v in data.items():
                normalized = k.lower().replace("-", "_")
                if any(sub in normalized for sub in cls._SENSITIVE_SUBSTRINGS):
                    result[k] = "***REDACTED***"
                else:
                    result[k] = cls._redact_sensitive(v)
            return result
        if isinstance(data, list):
            return [cls._redact_sensitive(item) for item in data]
        return data

    @contextmanager
    def log_api_execution(
        self,
        endpoint: str,
        method: str,
        parameters: dict[str, Any],
    ) -> Generator[None, None, None]:
        """Context manager for API execution with automatic timing."""
        start_time = time.time()
        self.log_api_call(endpoint, method, parameters, {})
        try:
            yield
        except Exception as e:
            self.log_error(
                f"API call failed: {str(e)}",
                type(e).__name__,
                traceback.format_exc(),
                {"endpoint": endpoint, "method": method},
            )
            raise
        finally:
            latency_ms = (time.time() - start_time) * 1000
            logger.debug(f"API call latency: {latency_ms:.2f}ms")
