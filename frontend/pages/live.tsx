import Link from 'next/link';

import { Shell } from '../components/v3/Shell';
import {
  Empty,
  ErrorBox,
  Label,
  Maturity,
  Mono,
  Panel,
  RunLink,
  Status,
  fmtMs,
  short,
} from '../components/v3/ui';
import { useLive, useStatus } from '../lib/v3/client';
import type { CEvent, Run } from '../lib/v3/types';

/** LIVE — what the observed system is doing now. Polls /api/v3/live every 3 s. */
export default function Live() {
  const live = useLive();
  const status = useStatus();
  const d = live.data;
  const events = (d?.recent_events ?? []) as CEvent[];
  const runs = (d?.recent_runs ?? []) as Run[];
  const active = (d?.active_runs ?? []) as Run[];
  const actors = Object.entries((d?.actors ?? {}) as Record<string, number>).sort(
    (a, b) => b[1] - a[1],
  );
  // system topology from recent events: actor → object hand-offs
  const topo = new Map<string, number>();
  for (const e of events)
    if (e.actor && e.object)
      topo.set(`${e.actor} → ${e.object}`, (topo.get(`${e.actor} → ${e.object}`) ?? 0) + 1);
  return (
    <Shell title="LIVE">
      <ErrorBox error={live.error} />
      <div className="grid grid-cols-12 gap-3">
        <Panel
          title="activity"
          className="col-span-12 lg:col-span-8"
          right={
            <Mono className="text-zinc-500">
              {status.data ? `${status.data.observations} observations` : ''}
            </Mono>
          }
        >
          {events.length === 0 ? (
            <Empty>
              No observations yet. Run <code>panoptis observe python your_app.py</code>, point an
              OTLP exporter at <code>/v1/traces</code>, or POST to <code>/api/v3/observations</code>
              .
            </Empty>
          ) : (
            <table className="w-full font-mono text-[11px]">
              <thead className="text-left text-zinc-500">
                <tr>
                  <th className="py-1">time</th>
                  <th>kind</th>
                  <th>operation</th>
                  <th>actor</th>
                  <th>status</th>
                  <th>duration</th>
                  <th>run</th>
                </tr>
              </thead>
              <tbody>
                {events.slice(0, 40).map((e) => (
                  <tr key={e.event_id} className="border-t border-zinc-900 hover:bg-zinc-900/60">
                    <td className="py-0.5 text-zinc-500">{e.time.start?.slice(11, 23) ?? '—'}</td>
                    <td className="text-sky-300">{e.kind}</td>
                    <td className="max-w-[260px] truncate">
                      <Link
                        href={`/timeline?run=${e.run_id}&event=${e.event_id}`}
                        className="hover:underline"
                      >
                        {e.operation}
                      </Link>
                    </td>
                    <td className="text-zinc-400">{e.actor ?? '—'}</td>
                    <td>
                      <Status s={e.status} />
                    </td>
                    <td className="text-zinc-500">{fmtMs(e.time.duration_ms)}</td>
                    <td>
                      {e.run_id ? (
                        <RunLink id={e.run_id} />
                      ) : (
                        <span className="text-amber-500" title="no run was declared">
                          none
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
        <div className="col-span-12 space-y-3 lg:col-span-4">
          <Panel title={`active runs (${active.length})`}>
            {active.length === 0 ? (
              <p className="text-xs text-zinc-500">none in progress</p>
            ) : (
              active.map((r) => (
                <div key={r.run_id} className="flex justify-between font-mono text-[11px]">
                  <RunLink id={r.run_id} /> <span>{r.name}</span>{' '}
                  <span>{r.event_count} events</span>
                </div>
              ))
            )}
          </Panel>
          <Panel title="recent runs">
            {runs.slice(0, 10).map((r) => (
              <div
                key={r.run_id}
                className="flex items-center justify-between gap-2 py-0.5 font-mono text-[11px]"
              >
                <RunLink id={r.run_id} />
                <span className="flex-1 truncate">{r.name}</span>
                <Status s={r.status} />
                <span className="text-zinc-500">{fmtMs(r.duration_ms)}</span>
                <span className="text-zinc-500" title="declared-link completeness">
                  {r.completeness.toFixed(2)}
                </span>
              </div>
            ))}
          </Panel>
          <Panel title="actors & resources (recent events)">
            {actors.map(([a, n]) => (
              <div key={a} className="flex justify-between font-mono text-[11px]">
                <span>{a}</span>
                <span className="text-zinc-500">{n}</span>
              </div>
            ))}
          </Panel>
          <Panel title="system topology (actor → object)">
            {[...topo.entries()]
              .sort((a, b) => b[1] - a[1])
              .slice(0, 14)
              .map(([k, n]) => (
                <div key={k} className="flex justify-between font-mono text-[11px]">
                  <span className="truncate">{k}</span>
                  <span className="text-zinc-500">{n}</span>
                </div>
              ))}
          </Panel>
          <Panel title={`unresolved observations (${d?.diagnostic_count ?? 0} diagnostics)`}>
            {((d?.unresolved ?? []) as Record<string, string>[]).slice(0, 8).map((x, i) => (
              <div key={i} className="font-mono text-[11px] text-amber-300/90">
                {x.code}: {x.message} <span className="text-zinc-600">{short(x.obs_id)}</span>
              </div>
            ))}
            {(d?.unresolved ?? []).length === 0 && (
              <p className="text-xs text-zinc-500">every observation was normalized</p>
            )}
          </Panel>
          <Panel title="capabilities">
            {((status.data?.capabilities ?? []) as { name: string; maturity: string }[]).map(
              (c) => (
                <div key={c.name} className="flex justify-between py-px font-mono text-[10px]">
                  <span className="text-zinc-400">{c.name}</span>
                  <Maturity m={c.maturity} />
                </div>
              ),
            )}
            {status.data && !status.data.reexecution_enabled && (
              <p className="mt-2 text-[10px] text-zinc-500">
                <Label>note</Label> server re-execution disabled: LAB offers L0/L1 replay only
              </p>
            )}
          </Panel>
        </div>
      </div>
    </Shell>
  );
}
