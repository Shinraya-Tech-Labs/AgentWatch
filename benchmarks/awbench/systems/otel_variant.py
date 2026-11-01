"""The tool_loop architecture instrumented with OpenTelemetry (GenAI conventions) instead
of the native SDK. Used by AWBench to compare normalization across sensors (H1).

``enriched=True`` adds AgentWatch's OpenTelemetry extension attributes (ADR-0018; an
AgentWatch convention, not an OpenTelemetry standard): the report write is marked as an
artifact operation with its content, and the model output / report input carry declared
information instance ids."""

from __future__ import annotations

import os
from typing import Any

from panoptis.sensors.base import FileSink
from panoptis.sensors.otel import AgentWatchSpanProcessor


def run_otel_tool_loop(
    gt: Any,
    scenario: str,
    retrieve: Any,
    summarize: Any,
    calculator: Any,
    *,
    enriched: bool = False,
) -> None:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Status, StatusCode

    provider = TracerProvider(resource=Resource.create({"service.name": "awbench-tool-loop"}))
    processor = AgentWatchSpanProcessor(FileSink(os.environ["AGENTWATCH_OBSERVE_FILE"]))
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("awbench")

    def node(label: str, kind: str) -> str:
        return gt.node(label, kind)

    with tracer.start_as_current_span(
        "invoke_agent solo",
        attributes={"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "solo"},
    ) as root:
        rid = node("agent_loop", "OPERATION")
        root.set_attribute("awbench_id", rid)
        gt.stack.append(rid)
        r = node("retrieve", "RETRIEVAL")
        with tracer.start_as_current_span(
            "query corpus",
            attributes={
                "db.system": "vectordb",
                "db.operation": "query",
                "db.name": "corpus",
                "awbench_id": r,
            },
        ):
            docs = retrieve("solar electricity")
        gt.data["origin_docs"] = [d["id"] for d in docs]
        prompt = "Context:\n" + "\n".join(f"- {d['text']}" for d in docs)
        m = node("summarize", "MODEL_INVOCATION")
        with tracer.start_as_current_span(
            "chat stub-summarizer-v1",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": "stub",
                "gen_ai.request.model": "summarizer-v1",
                "gen_ai.prompt": prompt,
                "awbench_id": m,
            },
        ) as span:
            out = summarize(prompt)
            span.set_attribute("gen_ai.completion", out)
            if enriched:
                span.set_attribute("panoptis.information.instance_id", "awbench:summary")
        gt.flow(r, m)
        t = node("calculate", "TOOL_INVOCATION")
        with tracer.start_as_current_span(
            "execute_tool calculator",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "calculator",
                "awbench_id": t,
            },
        ) as span:
            try:
                calculator("5 * 365")
            except Exception as exc:  # noqa: BLE001 - recorded on the span
                span.set_status(Status(StatusCode.ERROR, str(exc)))
        w = node("write_report", "STATE_MUTATION")
        report_attrs: dict[str, Any] = {"awbench_id": w}
        if enriched:
            report_attrs.update(
                {
                    "panoptis.artifact.operation": "create",
                    "panoptis.artifact.id": "report.md",
                    "panoptis.artifact.content": "# Report\n" + out,
                    "panoptis.information.source": "awbench:summary",
                }
            )
        with tracer.start_as_current_span("write report", attributes=report_attrs):
            pass
        gt.flow(m, w)
        gt.data["final_output"] = w
        gt.stack.pop()
    provider.shutdown()
