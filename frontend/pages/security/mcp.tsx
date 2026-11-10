import { SecurityShell } from '../../components/security/SecurityShell';
import {
  Card,
  MonitorStatus,
  QueryState,
  Table,
  Td,
  Time,
  Verdict,
} from '../../components/security/ui';
import { useDiscovery, useMcp } from '../../lib/protect/client';

export default function McpPage() {
  const mcp = useMcp();
  const discovery = useDiscovery();
  const configured = (discovery.data?.inventory?.items ?? []).filter(
    (i) =>
      i.kind === 'mcp_server' || i.runtime === 'mcp-server' || i.runtime === 'agentwatch-gateway',
  );
  return (
    <SecurityShell title="MCP">
      <h1 className="text-base font-semibold text-zinc-100">MCP gateway</h1>
      <Card title="Servers behind the PANOPTIS gateway">
        <QueryState
          q={mcp}
          isEmpty={!mcp.data?.servers.length}
          empty="No tool call has gone through a PANOPTIS MCP gateway in this tenant."
        >
          <Table
            label="gateway servers"
            head={['server', 'calls', 'verdicts', 'tools', 'last call']}
          >
            {mcp.data?.servers.map((s) => (
              <tr key={s.server}>
                <Td mono>{s.server}</Td>
                <Td mono>{s.decisions}</Td>
                <Td>
                  {Object.entries(s.verdicts).map(([v, n]) => (
                    <span key={v} className="mr-1">
                      <Verdict v={v} /> {n}
                    </span>
                  ))}
                </Td>
                <Td mono>{Object.keys(s.tools).join(', ')}</Td>
                <Td>
                  <Time t={s.last_seen} />
                </Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
      <Card title="Recent gateway calls">
        <QueryState q={mcp} isEmpty={!mcp.data?.recent.length} empty="None yet.">
          <Table
            label="recent gateway calls"
            head={['time', 'server', 'tool', 'verdict', 'reason']}
          >
            {mcp.data?.recent.map((r) => (
              <tr key={r.obs_id}>
                <Td>
                  <Time t={r.at} />
                </Td>
                <Td mono>{(r.source ?? '').replace(/^mcp:/, '')}</Td>
                <Td mono>{r.tool}</Td>
                <Td>
                  <Verdict v={r.verdict} />
                </Td>
                <Td>{r.reason}</Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
      <Card title="Configured MCP servers (discovery)">
        <QueryState
          q={discovery}
          isEmpty={!configured.length}
          empty="No discovery inventory with MCP servers recorded."
        >
          <Table
            label="configured mcp servers"
            head={['status', 'name', 'runtime', 'source', 'why']}
          >
            {configured.map((i, n) => (
              <tr key={`${i.source}-${i.name}-${n}`}>
                <Td>
                  <MonitorStatus s={i.status} />
                </Td>
                <Td mono>{i.name}</Td>
                <Td mono>{i.runtime}</Td>
                <Td mono>{i.source}</Td>
                <Td>{i.reason}</Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
    </SecurityShell>
  );
}
