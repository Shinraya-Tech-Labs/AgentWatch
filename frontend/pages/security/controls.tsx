import { useState } from 'react';

import { SecurityShell } from '../../components/security/SecurityShell';
import { Card, ErrorNote, QueryState, Table, Td, Time } from '../../components/security/ui';
import { useControl, useControlAction } from '../../lib/protect/client';

export default function ControlsPage() {
  const control = useControl();
  const action = useControlAction();
  const [reason, setReason] = useState('');
  const [confirm, setConfirm] = useState(false);
  const c = control.data;
  const run = (path: string) => {
    if (!reason.trim()) return;
    action.mutate({ path, reason: reason.trim() });
    setConfirm(false);
  };
  return (
    <SecurityShell title="Controls">
      <h1 className="text-base font-semibold text-zinc-100">Emergency controls</h1>
      <Card title="Emergency stop (all tenants)">
        <QueryState q={control}>
          {c ? (
            <div className="space-y-2 text-sm">
              <p
                role="status"
                className={c.killed ? 'font-semibold text-rose-300' : 'text-zinc-300'}
              >
                {c.killed
                  ? `ACTIVE since ${c.killed_at ?? '?'} by ${c.kill_actor ?? '?'}: ${c.kill_reason ?? ''}`
                  : 'Not active. Protected agents run normally.'}
              </p>
              <p className="text-[12px] text-zinc-400">
                Stops every protected agent before its next tool call. Running tools finish. Pending
                approvals are withdrawn. Operator key required.
              </p>
              <label className="block">
                reason (recorded)
                <input
                  aria-label="control reason"
                  className="mt-1 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              {c.killed ? (
                <button
                  type="button"
                  disabled={!reason.trim() || action.isPending}
                  onClick={() => run('/reset')}
                  className="rounded bg-emerald-700 px-3 py-1 text-white disabled:opacity-50"
                >
                  Lift emergency stop
                </button>
              ) : confirm ? (
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!reason.trim() || action.isPending}
                    onClick={() => run('/kill')}
                    className="rounded bg-rose-700 px-3 py-1 text-white disabled:opacity-50"
                  >
                    Confirm: stop all agents
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirm(false)}
                    className="rounded border border-zinc-700 px-3 py-1"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  disabled={!reason.trim()}
                  onClick={() => setConfirm(true)}
                  className="rounded bg-rose-800 px-3 py-1 text-white disabled:opacity-50"
                >
                  Emergency stop…
                </button>
              )}
              {action.error ? <ErrorNote error={action.error} /> : null}
            </div>
          ) : null}
        </QueryState>
      </Card>
      <Card title="Suspensions in this tenant">
        <QueryState
          q={control}
          isEmpty={
            !c ||
            (!Object.keys(c.suspended_agents).length && !Object.keys(c.suspended_sessions).length)
          }
          empty="No agent or session is suspended. Suspend from the Agents view."
        >
          <Table label="suspensions" head={['kind', 'id', 'reason', 'by', 'since', 'action']}>
            {c
              ? [
                  ...Object.entries(c.suspended_agents).map(
                    ([id, m]) => ['agents', id, m] as const,
                  ),
                  ...Object.entries(c.suspended_sessions).map(
                    ([id, m]) => ['sessions', id, m] as const,
                  ),
                ].map(([kind, id, m]) => (
                  <tr key={`${kind}-${id}`}>
                    <Td>{kind === 'agents' ? 'agent' : 'session'}</Td>
                    <Td mono>{id}</Td>
                    <Td>{m.reason}</Td>
                    <Td mono>{m.actor}</Td>
                    <Td>
                      <Time t={m.at} />
                    </Td>
                    <Td>
                      <button
                        type="button"
                        className="text-[11px] text-sky-400 underline"
                        onClick={() =>
                          action.mutate({
                            path: `/${kind}/${encodeURIComponent(id)}/resume`,
                            reason: reason.trim() || 'resumed from dashboard',
                          })
                        }
                      >
                        resume
                      </button>
                    </Td>
                  </tr>
                ))
              : null}
          </Table>
        </QueryState>
      </Card>
    </SecurityShell>
  );
}
