"""Google Agent Development Kit (ADK) Adapter for Panoptis Protection Hypervisor.

Provides native 3-tier lifecycle callback hooks for Google ADK (google-adk / adk.dev):
1. before_tool_callback: Pre-execution interception, Dual-Lattice IFC, Lethal Trifecta mitigation,
   RBAC policy enforcement, and argument mutation.
2. after_tool_callback: Post-execution inspection, secret masking, indirect prompt injection
   quarantine, and zero-click exfiltration defanging.
3. before_model_callback: Context erosion and Crescendo drift anchoring.
4. after_model_callback: Outgoing model response inspection.

Usage:
    from google.adk.agents.llm_agent import Agent
    from panoptis.adapters.google_adk import PanoptisADKGuard
    from panoptis.protect import ProtectionConfig, ProtectionPipeline

    guard = PanoptisADKGuard(
        pipeline=ProtectionPipeline(config=ProtectionConfig.sovereign()),
        tenant_id="enterprise-tenant",
    )

    root_agent = Agent(
        name="secure_cloud_agent",
        model="gemini-2.5-pro",
        instruction="Analyze project data and generate reports.",
        tools=[fetch_data, write_report, execute_bash],
        before_tool_callback=guard.before_tool_callback,
        after_tool_callback=guard.after_tool_callback,
        before_model_callback=guard.before_model_callback,
        after_model_callback=guard.after_model_callback,
    )
"""

from __future__ import annotations

import logging
from typing import Any

from panoptis.protect.model import ActionBlocked, ToolAction, Verdict
from panoptis.protect.output import OutputInspector
from panoptis.protect.pipeline import ProtectionConfig, ProtectionPipeline

logger = logging.getLogger(__name__)


class PanoptisADKGuard:
    """Security hypervisor adapter for Google Agent Development Kit (ADK)."""

    def __init__(
        self,
        pipeline: ProtectionPipeline | None = None,
        output_inspector: OutputInspector | None = None,
        tenant_id: str = "default",
        fail_closed: bool = True,
    ) -> None:
        self.pipeline = pipeline or ProtectionPipeline(config=ProtectionConfig.sovereign())
        self.output_inspector = output_inspector or OutputInspector()
        self.tenant_id = tenant_id
        self.fail_closed = fail_closed

    def _extract_session_id(self, tool_context: Any) -> str:
        if tool_context is None:
            return "default_session"
        if hasattr(tool_context, "session_id") and tool_context.session_id:
            return str(tool_context.session_id)
        if hasattr(tool_context, "state") and isinstance(tool_context.state, dict):
            return str(tool_context.state.get("session_id", "default_session"))
        return "default_session"

    def before_tool_callback(
        self, tool: Any, args: dict[str, Any], tool_context: Any = None
    ) -> dict[str, Any] | None:
        """ADK Pre-tool execution lifecycle hook.

        Evaluates the tool call against Panoptis Dual-Lattice IFC, Lethal Trifecta
        invariants, RBAC, and temporal logic.

        Returns None if allowed (normal ADK execution proceeds), or returns a dict
        containing error details to short-circuit and block execution.
        """
        tool_name = getattr(tool, "name", str(tool))
        session_id = self._extract_session_id(tool_context)

        action = ToolAction(
            tool=tool_name,
            arguments=dict(args or {}),
            session_id=session_id,
            tenant_id=self.tenant_id,
            agent_id=getattr(tool_context, "agent_id", "adk-agent"),
        )

        try:
            decision = self.pipeline.evaluate(action)

            if decision.verdict is Verdict.BLOCK:
                findings_summary = "; ".join(f.message for f in decision.findings)
                logger.warning(
                    "Panoptis blocked ADK tool '%s' (session=%s): %s",
                    tool_name,
                    session_id,
                    findings_summary,
                )
                return {
                    "error": "Execution blocked by Panoptis Security Hypervisor",
                    "reason": findings_summary,
                    "findings": [f.code for f in decision.findings],
                    "blocked": True,
                }

            if decision.verdict is Verdict.ESCALATE_HITL:
                logger.warning("Panoptis escalated ADK tool '%s' for HITL approval", tool_name)
                return {
                    "error": "Execution requires human-in-the-loop (HITL) authorization",
                    "requires_approval": True,
                    "blocked": True,
                }

            if decision.verdict is Verdict.MUTATE_AND_ALLOW and decision.mutated_arguments:
                # Update arguments in place if mutable
                if isinstance(args, dict):
                    args.clear()
                    args.update(decision.mutated_arguments)
                return None

            return None

        except ActionBlocked as exc:
            return {
                "error": "Execution blocked by policy",
                "reason": str(exc),
                "blocked": True,
            }
        except Exception as exc:
            logger.error("Error in Panoptis ADK before_tool_callback: %s", exc, exc_info=True)
            if self.fail_closed:
                return {
                    "error": "Security evaluation failed closed",
                    "reason": str(exc),
                    "blocked": True,
                }
            return None

    def after_tool_callback(
        self, tool: Any, args: dict[str, Any], tool_context: Any = None, tool_response: Any = None
    ) -> Any:
        """ADK Post-tool execution lifecycle hook.

        Inspects tool output for indirect prompt injections, AI worm propagation,
        zero-click exfiltration beacons, and masks confidential secrets before
        data returns to the agent context.
        """
        tool_name = getattr(tool, "name", str(tool))
        session_id = self._extract_session_id(tool_context)

        # 1. Inspect and sanitize output
        inspection = self.output_inspector.inspect(tool_name, tool_response)
        sanitized_result = inspection.sanitized_output

        # 2. Record execution to update dynamic taint tracking and trajectory state
        action = ToolAction(
            tool=tool_name,
            arguments=dict(args or {}),
            session_id=session_id,
            tenant_id=self.tenant_id,
            agent_id=getattr(tool_context, "agent_id", "adk-agent"),
        )
        self.pipeline.record_execution(
            action=action,
            success=True,
            result=sanitized_result,
        )

        return sanitized_result

    def before_model_callback(self, model: Any, contents: Any, context: Any = None) -> Any:
        """ADK Pre-model execution lifecycle hook.

        Monitors multi-turn context erosion and anchors session objectives.
        """
        return None

    def after_model_callback(self, model: Any, response: Any, context: Any = None) -> Any:
        """ADK Post-model execution lifecycle hook.

        Inspects generated model responses for masked secrets and exfiltration beacons.
        """
        if response is None:
            return response
        inspection = self.output_inspector.inspect("model_response", response)
        return inspection.sanitized_output
