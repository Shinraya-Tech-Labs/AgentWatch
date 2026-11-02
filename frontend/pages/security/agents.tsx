import { SecurityShell } from '../../components/security/SecurityShell';
import {
  Card,
  ErrorNote,
  MonitorStatus,
  QueryState,
  Table,
  Td,
  Time,
} from '../../components/security/ui';
import { useAgents, useControlAction, useDiscovery } from '../../lib/protect/client';

export default function AgentsPage() {
  const agents = useAgents();
  const discovery = useDiscovery();
  const action = useControlAction();
  const toggle = (kind: 'agents' | 'sessions', id: string, suspended: boolean) => {
    const reason = window.prompt(`${suspended ? 'Resume' : 'Suspend'} ${id}: reason (recorded)`);
    if (reason)
      action.mutate({
        path: `/${kind}/${encodeURIComponent(id)}/${suspended ? 'resume' : 'suspend'}`,
        reason,
      });
  };
  const items = discovery.data?.inventory?.items ?? [];
  return (
    <SecurityShell title="Agents">
      <h1 className="text-base font-semibold text-zinc-100">Agents and inventory</h1>
      <Card title="Agents with protected calls">
        <p className="mb-2 text-[12px] text-zinc-400">
          Agents whose tool calls reached the AgentWatch pipeline in this tenant.
        </p>
        {action.error ? <ErrorNote error={action.error} /> : null}
        <QueryState
          q={agents}
          isEmpty={!agents.data?.agents.length}
          empty="No agent has made a protected call yet."
        >
          <Table
            label="agents"
            head={[
              'agent',
              'sessions',
              'decisions',
              'blocked',
              'escalated',
              'last seen',
              'state',
              '',
            ]}
          >
            {agents.data?.agents.map((a) => (
              <tr key={a.agent_id}>
                <Td mono>{a.agent_id}</Td>
                <Td>
                  <ul>
                    {a.sessions.map((s) => (
                      <li key={s.session_id} className="font-mono text-[12px]">
                        {s.session_id}{' '}
                        {s.objective ? (
                          <span className="text-zinc-400">— {s.objective}</span>
                        ) : null}{' '}
                        {s.suspended ? <span className="text-rose-300">(suspended)</span> : null}{' '}
                        <button
                          type="button"
                          className="text-[11px] text-sky-400 underline"
                          onClick={() => toggle('sessions', s.session_id, s.suspended)}
                        >
                          {s.suspended ? 'resume' : 'suspend'}
                        </button>
                      </li>
                    ))}
                  </ul>
                </Td>
                <Td mono>{a.decisions}</Td>
                <Td mono>{a.blocked}</Td>
                <Td mono>{a.escalated}</Td>
                <Td>
                  <Time t={a.last_seen} />
                </Td>
                <Td>
                  {a.suspended ? (
                    <span className="text-rose-300">suspended</span>
                  ) : (
                    <span className="text-zinc-300">active</span>
                  )}
                </Td>
                <Td>
                  <button
                    type="button"
                    className="rounded border border-zinc-700 px-2 py-0.5 text-[11px]"
                    onClick={() => toggle('agents', a.agent_id, a.suspended)}
                    aria-label={`${a.suspended ? 'resume' : 'suspend'} agent ${a.agent_id}`}
                  >
                    {a.suspended ? 'resume' : 'suspend'}
                  </button>
                </Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
      <Card
        title="Discovered runtimes and MCP servers"
        right={
          <span className="text-[11px] text-zinc-400">
            recorded <Time t={discovery.data?.recorded_at} />
          </span>
        }
      >
        <p className="mb-2 text-[12px] text-zinc-400">
          From <code className="font-mono">panoptis protect discover --record</code>. Detection is
          not protection: only MONITORED items are enforced on.
        </p>
        <QueryState
          q={discovery}
          isEmpty={!items.length}
          empty="No inventory recorded for this tenant yet (run panoptis protect discover --record)."
        >
          <Table
            label="discovery"
            head={['status', 'kind', 'runtime', 'name', 'source', 'confidence', 'why']}
          >
            {items.map((i, n) => (
              <tr key={`${i.source}-${i.name}-${n}`}>
                <Td>
                  <MonitorStatus s={i.status} />
                </Td>
                <Td>{i.kind}</Td>
                <Td mono>{i.runtime}</Td>
                <Td mono>{i.name}</Td>
                <Td mono>{i.source}</Td>
                <Td mono>{i.confidence.toFixed(2)}</Td>
                <Td>{i.reason}</Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
    </SecurityShell>
  );
}
