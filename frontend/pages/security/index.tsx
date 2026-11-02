import Link from 'next/link';

import { SecurityShell } from '../../components/security/SecurityShell';
import { Card, QueryState, Stat, Table, Td, Time, Verdict } from '../../components/security/ui';
import { useDecisions, useStatus } from '../../lib/protect/client';

export default function SecurityOverview() {
  const status = useStatus();
  const recent = useDecisions({ limit: 15 });
  const s = status.data;
  return (
    <SecurityShell title="Overview">
      <h1 className="sr-only">Security overview</h1>
      <QueryState q={status}>
        {s ? (
          <div className="grid grid-cols-2 gap-2 md:grid-cols-6">
            <Stat label="decisions (recent)" value={s.recent_decisions} />
            <Stat label="allowed" value={s.verdicts.allow ?? 0} tone="emerald" />
            <Stat label="blocked" value={s.verdicts.block ?? 0} tone="rose" />
            <Stat label="escalated" value={s.verdicts.escalate_hitl ?? 0} tone="amber" />
            <Stat label="mutated" value={s.verdicts.mutate_and_allow ?? 0} tone="sky" />
            <Stat
              label="emergency stop"
              value={s.killed ? 'ACTIVE' : s.killed === false ? 'off' : 'unknown'}
              tone={s.killed ? 'rose' : 'zinc'}
            />
          </div>
        ) : null}
      </QueryState>
      {s ? (
        <div className="grid gap-2 md:grid-cols-3">
          <Card title="Runtime protection">
            <dl className="space-y-1 text-sm">
              <div>
                <dt className="inline text-zinc-400">last decision: </dt>
                <dd className="inline">
                  <Time t={s.last_decision_at} />
                </dd>
              </div>
              <div>
                <dt className="inline text-zinc-400">suspended agents / sessions: </dt>
                <dd className="inline font-mono">
                  {s.suspended_agents} / {s.suspended_sessions}
                </dd>
              </div>
            </dl>
          </Card>
          <Card title="RBAC policy">
            {s.policy.configured ? (
              <p className="text-sm">
                active, digest <code className="font-mono">{s.policy.digest}</code> ·{' '}
                <Link className="text-sky-400 underline" href="/security/policy">
                  view
                </Link>
              </p>
            ) : (
              <p className="text-sm text-amber-300">
                No RBAC policy is configured: tool authorization is not enforced.
              </p>
            )}
          </Card>
          <Card title="Evidence">
            {s.evidence.head ? (
              <p className="text-sm">
                {s.evidence.segments} sealed segments, head #{s.evidence.head.seq}{' '}
                <code className="font-mono text-[11px]">
                  {s.evidence.head.segment_hash.slice(0, 16)}…
                </code>{' '}
                ·{' '}
                <Link className="text-sky-400 underline" href="/security/evidence">
                  verify
                </Link>
              </p>
            ) : (
              <p className="text-sm text-zinc-400">No sealed evidence yet.</p>
            )}
          </Card>
        </div>
      ) : null}
      <Card
        title="Recent decisions"
        right={
          <Link className="text-[11px] text-sky-400 underline" href="/security/decisions">
            all decisions
          </Link>
        }
      >
        <QueryState
          q={recent}
          isEmpty={!recent.data?.decisions.length}
          empty="No protected tool calls yet."
        >
          <Table
            label="recent decisions"
            head={['time', 'verdict', 'tool', 'agent', 'session', 'reason']}
          >
            {recent.data?.decisions.map((d) => (
              <tr key={d.obs_id}>
                <Td>
                  <Time t={d.at} />
                </Td>
                <Td>
                  <Verdict v={d.verdict} />
                </Td>
                <Td mono>{d.tool}</Td>
                <Td mono>{d.agent_id}</Td>
                <Td mono>{d.session_id}</Td>
                <Td>{d.reason}</Td>
              </tr>
            ))}
          </Table>
        </QueryState>
      </Card>
    </SecurityShell>
  );
}
