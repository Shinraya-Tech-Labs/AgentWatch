import { useRouter } from 'next/router';
import { useEffect, useMemo } from 'react';

import { EventInspector } from '../components/v3/EventInspector';
import { ProofAndGapsVisualizer } from '../components/v3/ProofAndGapsVisualizer';
import { Shell } from '../components/v3/Shell';
import {
  Empty,
  ErrorBox,
  Label,
  Maturity,
  Mono,
  Panel,
  Status,
  fmtMs,
  short,
} from '../components/v3/ui';
import { useEvent, useRun } from '../lib/v3/client';
import type { CEvent } from '../lib/v3/types';

const KIND_COLOR: Record<string, string> = {
  MODEL_INVOCATION: '#a78bfa',
  TOOL_INVOCATION: '#f59e0b',
  RETRIEVAL: '#38bdf8',
  MEMORY_ACCESS: '#34d399',
  MESSAGE: '#f472b6',
  DELEGATION: '#fb7185',
  STATE_MUTATION: '#facc15',
  OPERATION: '#52525b',
  LIFECYCLE: '#3f3f46',
  FAILURE: '#ef4444',
  EXTERNAL_IO: '#2dd4bf',
};

/** TIMELINE — a run's execution history: swimlanes per actor plus the reconstructed tree. */
export default function Timeline() {
  const router = useRouter();
  const run = typeof router.query.run === 'string' ? router.query.run : undefined;
  const selected = typeof router.query.event === 'string' ? router.query.event : null;
  const q = useRun(run);
  // links that carry only an event id (e.g. QUERY evidence) resolve their run here
  const orphan = useEvent(!run ? selected : null);
  useEffect(() => {
    const rid = orphan.data?.event.run_id;
    if (!run && rid)
      router.replace({ pathname: '/timeline', query: { run: rid, event: selected } }, undefined, {
        shallow: true,
      });
  }, [orphan.data, run, selected, router]);
  const select = (id: string) =>
    router.push({ pathname: '/timeline', query: { run, event: id } }, undefined, { shallow: true });

  const lanes = useMemo(() => {
    const evs = (q.data?.events ?? []).filter((e) => e.time.start);
    if (!evs.length) return null;
    const t0 = Math.min(...evs.map((e) => Date.parse(e.time.start!)));
    const t1 = Math.max(...evs.map((e) => Date.parse(e.time.end ?? e.time.start!)));
    const span = Math.max(1, t1 - t0);
    const byActor = new Map<string, CEvent[]>();
    for (const e of evs) {
      if (e.kind === 'LIFECYCLE') continue;
      const a = e.actor ?? '(no actor declared)';
      if (!byActor.has(a)) byActor.set(a, []);
      byActor.get(a)!.push(e);
    }
    return { t0, span, byActor: [...byActor.entries()] };
  }, [q.data]);

  const byId = useMemo(() => new Map((q.data?.events ?? []).map((e) => [e.event_id, e])), [q.data]);
  const d = q.data;
  return (
    <Shell title="TIMELINE" runPicker>
      {!run && <Empty>Select a run.</Empty>}
      <ErrorBox error={q.error} />
      {d && (
        <div className="grid grid-cols-12 gap-3">
          <div className="col-span-12 space-y-3 xl:col-span-7">
            <Panel
              title={`run ${short(d.run.run_id)} · ${d.run.name}`}
              right={<Status s={d.run.status} />}
            >
              <div className="flex flex-wrap gap-x-4 font-mono text-[11px] text-zinc-400">
                <span>{d.run.started_at?.replace('T', ' ').slice(0, 23)}</span>
                <span>duration {fmtMs(d.run.duration_ms)}</span>
                <span>{d.run.event_count} events</span>
                <span>errors {d.run.error_events}</span>
                <span>retries {d.retries}</span>
                <span title="declared parent links that resolved to observed events">
                  completeness {d.run.completeness.toFixed(2)} ({d.run.resolved_links}/
                  {d.run.declared_links})
                </span>
                <span>
                  tokens {d.run.tokens_in}/{d.run.tokens_out}
                </span>
                <span>version {d.run.system_version}</span>
              </div>
              {Object.keys(d.missing_facts).length > 0 && (
                <div className="mt-1 font-mono text-[11px] text-amber-400/90">
                  missing facts (not invented): {JSON.stringify(d.missing_facts)}
                </div>
              )}
              {d.motif_instances.length > 0 && (
                <div className="mt-2 space-y-0.5">
                  {d.motif_instances.map((m) => (
                    <div key={m.record_id} className="font-mono text-[11px] text-violet-300">
                      {m.motif_id} {m.motif_name}: {m.explanation} <Maturity m={m.maturity} />
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            {lanes && (
              <Panel title="swimlanes (by actor)">
                <div className="space-y-1">
                  {lanes.byActor.map(([actor, evs]) => (
                    <div key={actor} className="flex items-center gap-2">
                      <div
                        className="w-40 shrink-0 truncate font-mono text-[10px] text-zinc-400"
                        title={actor}
                      >
                        {actor}
                      </div>
                      <div className="relative h-5 flex-1 rounded bg-zinc-900">
                        {evs.map((e) => {
                          const s = Date.parse(e.time.start!);
                          const en = Date.parse(e.time.end ?? e.time.start!);
                          const left = ((s - lanes.t0) / lanes.span) * 100;
                          const width = Math.max(0.6, ((en - s) / lanes.span) * 100);
                          return (
                            <button
                              key={e.event_id}
                              title={`${e.kind} ${e.operation} · ${e.status} · ${fmtMs(e.time.duration_ms)}`}
                              onClick={() => select(e.event_id)}
                              className={`absolute top-0.5 h-4 rounded-sm ${selected === e.event_id ? 'ring-2 ring-white' : ''}`}
                              style={{
                                left: `${left}%`,
                                width: `${width}%`,
                                background: KIND_COLOR[e.kind] ?? '#71717a',
                                opacity:
                                  e.kind === 'OPERATION' ? 0.35 : e.status === 'ERROR' ? 1 : 0.85,
                                outline: e.status === 'ERROR' ? '1px solid #ef4444' : undefined,
                              }}
                            />
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 font-mono text-[10px] text-zinc-500">
                  {Object.entries(KIND_COLOR).map(([k, c]) => (
                    <span key={k}>
                      <span style={{ color: c }}>■</span> {k.toLowerCase()}
                    </span>
                  ))}
                </div>
              </Panel>
            )}
            <Panel title="Proof & Gaps Verification (EvidenceLab)">
              <ProofAndGapsVisualizer
                events={d.events}
                selectedEventId={selected}
                onSelectEvent={select}
              />
            </Panel>
            <Panel title="execution tree (declared structure)">
              <div className="max-h-[520px] overflow-auto">
                {d.tree.map(({ depth, event_id }, i) => {
                  const e = byId.get(event_id);
                  if (!e) return null;
                  return (
                    <button
                      key={`${event_id}-${i}`}
                      onClick={() => select(event_id)}
                      className={`flex w-full items-center gap-2 py-px text-left font-mono text-[11px] hover:bg-zinc-900 ${selected === event_id ? 'bg-zinc-800' : ''}`}
                      style={{ paddingLeft: depth * 16 }}
                    >
                      <Label tone={e.kind === 'OPERATION' ? 'zinc' : 'sky'}>{e.kind}</Label>
                      <span className="truncate">{e.operation}</span>
                      {e.actor && <span className="text-zinc-500">{e.actor}</span>}
                      <Status s={e.status} />
                      <Mono className="ml-auto text-zinc-600">{fmtMs(e.time.duration_ms)}</Mono>
                    </button>
                  );
                })}
              </div>
            </Panel>
          </div>
          <div className="col-span-12 xl:col-span-5">
            <EventInspector eventId={selected} runId={run} onSelect={select} />
          </div>
        </div>
      )}
    </Shell>
  );
}
