"""MCP (Model Context Protocol) message tap.

Record JSON-RPC messages exchanged with an MCP server::

    tap = MCPTap(sink, server="github")
    tap.record("client->server", request_dict)
    tap.record("server->client", response_dict)

Requests and responses are paired later by the JSON-RPC ``id`` they declare.
"""

from __future__ import annotations

from typing import Any

from panoptis.sensors.base import ObservationSink, Sensor
from panoptis.sensors.native.recorder import current_run, current_span


class MCPTap(Sensor):
    sensor_type = "mcp"
    version = "1"

    def __init__(
        self,
        sink: ObservationSink,
        server: str,
        session_id: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        super().__init__(sink, tenant_id)
        self.server = server
        self.session_id = session_id or self.ctx.ref.instance_id

    def record(self, direction: str, message: dict[str, Any]) -> None:
        span = current_span()
        run = current_run()
        self.ctx.emit(
            "mcp.message",
            {"direction": direction, "server": self.server, "message": message},
            declared_ids={
                "mcp_session": self.session_id,
                "mcp_server": self.server,
                "jsonrpc_id": str(message.get("id")) if message.get("id") is not None else None,
                "parent_span_id": span.span_id if span else None,
                "run_id": run.run_id if run else None,
            },
        )
