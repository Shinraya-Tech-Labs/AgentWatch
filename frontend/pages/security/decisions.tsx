import { useState } from 'react';

import { SecurityShell } from '../../components/security/SecurityShell';
import {
  Card,
  ErrorNote,
  QueryState,
  Table,
  Td,
  Time,
  Verdict,
} from '../../components/security/ui';
import {
  useCallDetail,
  useDecisions,
  useStageEvents,
  type DecisionFilters,
} from '../../lib/protect/client';

const STAGES: [string, string][] = [
  ['threat', 'threat check'],
  ['rbac', 'permissions (RBAC)'],
  ['loop', 'repeated calls'],
  ['intent', 'off the stated task'],
  ['hitl', 'human approval'],
  ['killswitch', 'emergency stop'],
  ['identity', 'agent identity'],
  ['output', 'tool output'],
];

export default function Decisions() {
  const [filters, setFilters] = useState<DecisionFilters>({});
  const [selected, setSelected] = useState<string | undefined>();
  const decisions = useDecisions(filters);
  const loops = useStageEvents('loops');
  const intent = useStageEvents('intent');
  const set = (k: keyof DecisionFilters, v: string) =>
    setFilters((f) => ({ ...f, [k]: v || undefined }));
  return (
    <SecurityShell title="Decisions">
      <h1 className="text-base font-semibold text-zinc-100">Decisions</h1>
      <Card title="Filter">
        <form className="flex flex-wrap gap-3 text-sm" onSubmit={(e) => e.preventDefault()}>
          <label className="flex items-center gap-1">
            verdict
            <select
              aria-label="verdict"
              className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5"
              value={filters.verdict ?? ''}
              onChange={(e) => set('verdict', e.target.value)}
            >
              <option value="">any</option>
              <option value="allow">allow</option>
              <option value="block">block</option>
              <option value="escalate_hitl">escalate_hitl</option>
              <option value="mutate_and_allow">mutate_and_allow</option>
            </select>
          </label>
          <label className="flex items-center gap-1">
            stage
            <select
              aria-label="stage"
              className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5"
              value={filters.stage ?? ''}
              onChange={(e) => set('stage', e.target.value)}
            >
              <option value="">any</option>
              {STAGES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-1">
            tool
            <input
              aria-label="tool"
              className="w-32 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 font-mono"
              value={filters.tool ?? ''}
              onChange={(e) => set('tool', e.target.value)}
            />
          </label>
          <label className="flex items-center gap-1">
            agent
            <input
              aria-label="agent"
              className="w-32 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 font-mono"
              value={filters.agent_id ?? ''}
              onChange={(e) => set('agent_id', e.target.value)}
            />
          </label>
        </form>
      </Card>
      <div className="grid gap-3 lg:grid-cols-[2fr_1fr]">
        <Card title={`Decisions (${decisions.data?.count ?? 0})`}>
          <QueryState
            q={decisions}
            isEmpty={!decisions.data?.decisions.length}
            empty="No decisions match."
          >
            <Table
              label="decisions"
              head={['time', 'verdict', 'tool', 'agent', 'risk', 'reason', 'details']}
            >
              {decisions.data?.decisions.map((d) => (
                <tr key={d.obs_id} className={selected === d.call_id ? 'bg-zinc-900' : ''}>
                  <Td>
                    <Time t={d.at} />
                  </Td>
                  <Td>
                    <Verdict v={d.verdict} />
                    {d.mode === 'observe' && d.would_block ? (
                      <span className="ml-1 text-[11px] text-amber-300">would block (observe)</span>
                    ) : null}
                  </Td>
                  <Td mono>{d.tool}</Td>
                  <Td mono>{d.agent_id}</Td>
                  <Td mono>{d.risk_score.toFixed(2)}</Td>
                  <Td>{d.reason}</Td>
                  <Td>
                    <button
                      type="button"
                      className="text-[11px] text-sky-400 underline"
                      onClick={() => setSelected(d.call_id)}
                      aria-label={`details of ${d.tool} call ${d.call_id}`}
                    >
                      details
                    </button>
                  </Td>
                </tr>
              ))}
            </Table>
          </QueryState>
        </Card>
        <CallPanel callId={selected} />
      </div>
      {/* shown only when there is something to see: an empty panel explained nothing */}
      {loops.data?.events.length || intent.data?.events.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {loops.data?.events.length ? (
            <StagePanel title="Repeated calls" q={loops} empty="" />
          ) : null}
          {intent.data?.events.length ? (
            <StagePanel title="Calls off the stated task" q={intent} empty="" />
          ) : null}
        </div>
      ) : null}
    </SecurityShell>
  );
}

function CallPanel({ callId }: { callId?: string }) {
  const detail = useCallDetail(callId);
  if (!callId)
    return (
      <Card title="Call detail">
        <p className="text-sm text-zinc-400">
          Select a decision to see its findings, approval, execution and output inspection.
        </p>
      </Card>
    );
  const d = detail.data?.decisions[0];
  return (
    <Card title="Call detail">
      {detail.error ? <ErrorNote error={detail.error} /> : null}
      {d ? (
        <div className="space-y-2 text-sm">
          <p>
            <Verdict v={d.verdict} /> <code className="font-mono">{d.tool}</code> by{' '}
            <code className="font-mono">{d.agent_id}</code>
          </p>
          <p className="font-mono text-[11px] text-zinc-400">
            call {d.call_id} · policy {d.policy_version ?? 'none'} · rules{' '}
            {d.threat_rules_version?.slice(0, 18)}
          </p>
          <ul className="space-y-1" aria-label="findings">
            {d.findings.map((f, i) => (
              <li key={i} className="rounded border border-zinc-800 px-2 py-1">
                <span className="font-mono text-[11px] text-zinc-400">
                  {f.stage}:{f.code} → {f.recommends}
                </span>
                <div>{f.message}</div>
              </li>
            ))}
          </ul>
          <p className="text-[12px] text-zinc-300">
            executed: {detail.data!.executions.length ? 'yes' : 'no'} · approval:{' '}
            {d.approval_state ?? '—'} · output inspected:{' '}
            {detail.data!.outputs.length ? 'yes' : 'no'}
          </p>
        </div>
      ) : detail.isLoading ? (
        <p className="text-sm text-zinc-400">Loading…</p>
      ) : null}
    </Card>
  );
}

function StagePanel({
  title,
  q,
  empty,
}: {
  title: string;
  q: ReturnType<typeof useStageEvents>;
  empty: string;
}) {
  return (
    <Card title={title}>
      <QueryState q={q} isEmpty={!q.data?.events.length} empty={empty}>
        <ul className="space-y-1 text-sm" aria-label={title}>
          {q.data?.events.map((e) => (
            <li key={e.obs_id} className="rounded border border-zinc-800 px-2 py-1">
              <Time t={e.at} /> <Verdict v={e.verdict} />{' '}
              <code className="font-mono">{e.tool}</code>{' '}
              <span className="text-zinc-400">
                ({e.agent_id}/{e.session_id})
              </span>
              {e.findings.map((f, i) => (
                <div key={i} className="text-[12px]">
                  <span className="font-mono text-zinc-400">{f.code}</span> {f.message}
                </div>
              ))}
            </li>
          ))}
        </ul>
      </QueryState>
    </Card>
  );
}
