import React, { useState, useMemo, useCallback } from 'react';
import type { CEvent } from '../../lib/v3/types';

export type EvidenceVerdict =
  | 'supported'
  | 'refuted'
  | 'inconclusive'
  | 'conflicting_evidence';

export interface MissingFact {
  field: string;
  reason: string;
  required_for: string;
  candidate_sensors: string[];
  recoverability: string;
}

export interface ContractAssessment {
  contract: string;
  verdict: EvidenceVerdict;
  summary: string;
  support_obs_ids: string[];
  refute_obs_ids: string[];
  missing_facts: MissingFact[];
  remedies: string[];
  assumptions?: string[];
}

export interface SequenceGapInfo {
  sensor_id: string;
  seq_lost_start: number;
  seq_lost_end: number;
  count: number;
  t_before?: string | null;
  t_after?: string | null;
}

export interface SensorCoverageInfo {
  sensor_id: string;
  authoritative_for: string[];
  active: boolean;
  dropped_count: number;
  clock_uncertainty_ms?: number;
  gaps?: SequenceGapInfo[];
  sampling_rate?: number | null;
  drop_rate?: number;
}

export interface ProofAndGapsVisualizerProps {
  events?: CEvent[];
  assessments?: ContractAssessment[];
  coverage?: SensorCoverageInfo[];
  selectedEventId?: string | null;
  onSelectEvent?: (eventId: string) => void;
}

export const VERDICT_BADGE_STYLES: Record<
  EvidenceVerdict,
  { label: string; bg: string; text: string; border: string }
> = {
  supported: {
    label: 'SUPPORTED',
    bg: 'bg-emerald-500/15',
    text: 'text-emerald-400',
    border: 'border-emerald-500/40',
  },
  refuted: {
    label: 'REFUTED',
    bg: 'bg-rose-500/15',
    text: 'text-rose-400',
    border: 'border-rose-500/40',
  },
  inconclusive: {
    label: 'INCONCLUSIVE',
    bg: 'bg-amber-500/15',
    text: 'text-amber-400',
    border: 'border-amber-500/40',
  },
  conflicting_evidence: {
    label: 'CONFLICTING',
    bg: 'bg-purple-500/15',
    text: 'text-purple-400',
    border: 'border-purple-500/40',
  },
};

export function ProofAndGapsVisualizer({
  events = [],
  assessments = [],
  coverage = [],
  selectedEventId,
  onSelectEvent,
}: ProofAndGapsVisualizerProps) {
  const [activeContractIndex, setActiveContractIndex] = useState(0);
  const [copiedRemedy, setCopiedRemedy] = useState<string | null>(null);

  const activeAssessment = assessments[activeContractIndex] || assessments[0];

  const supportingWitnessSet = useMemo(() => {
    const set = new Set<string>();
    for (const a of assessments) {
      for (const id of a.support_obs_ids) set.add(id);
    }
    return set;
  }, [assessments]);

  const refutingWitnessSet = useMemo(() => {
    const set = new Set<string>();
    for (const a of assessments) {
      for (const id of a.refute_obs_ids) set.add(id);
    }
    return set;
  }, [assessments]);

  const handleCopyRemedy = useCallback((remedy: string) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(remedy);
      setCopiedRemedy(remedy);
      setTimeout(() => setCopiedRemedy(null), 2000);
    }
  }, []);

  const handleKeyDownSelect = useCallback(
    (e: React.KeyboardEvent, eventId: string) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onSelectEvent?.(eventId);
      }
    },
    [onSelectEvent]
  );

  return (
    <div
      data-testid="proof-and-gaps-visualizer"
      className="space-y-4 rounded-lg border border-zinc-800 bg-zinc-950 p-4 font-sans text-zinc-100"
    >
      {/* Header & Contract Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-3">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <h2 className="text-sm font-semibold tracking-wide text-zinc-100 uppercase">
            Proof & Gaps Visualizer
          </h2>
        </div>

        {assessments.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5" role="tablist">
            {assessments.map((a, idx) => {
              const badge = VERDICT_BADGE_STYLES[a.verdict] || VERDICT_BADGE_STYLES.inconclusive;
              const isActive = idx === activeContractIndex;
              return (
                <button
                  key={a.contract}
                  role="tab"
                  aria-selected={isActive}
                  tabIndex={0}
                  onClick={() => setActiveContractIndex(idx)}
                  className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-mono transition-all ${
                    isActive
                      ? 'bg-zinc-800 text-white shadow-sm ring-1 ring-zinc-700'
                      : 'bg-zinc-900/60 text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200'
                  }`}
                >
                  <span className="truncate max-w-[140px]">{a.contract}</span>
                  <span
                    className={`rounded px-1.5 py-0.2 text-[10px] font-bold border ${badge.bg} ${badge.text} ${badge.border}`}
                  >
                    {badge.label}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Main Grid: Witness Timeline & Coverage Waterfall */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left Column: Witness Subgraph & Execution Timeline */}
        <div className="lg:col-span-7 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-400">
              Witness Subgraph Timeline
            </h3>
            <div className="flex items-center gap-3 text-[11px] font-mono text-zinc-400">
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded bg-emerald-500" /> Supporting Witness
              </span>
              <span className="flex items-center gap-1">
                <span className="h-2 w-2 rounded bg-rose-500" /> Refuting Witness
              </span>
            </div>
          </div>

          <div
            className="space-y-2 max-h-[380px] overflow-y-auto pr-1"
            role="region"
            aria-label="Execution timeline"
          >
            {events.length === 0 ? (
              <div className="rounded border border-dashed border-zinc-800 p-6 text-center text-xs text-zinc-500 font-mono">
                No events recorded for this execution scope.
              </div>
            ) : (
              events.map((evt) => {
                const isSelected = selectedEventId === evt.event_id;
                const isSupporting = evt.derived_from.some((id) =>
                  supportingWitnessSet.has(id)
                );
                const isRefuting = evt.derived_from.some((id) =>
                  refutingWitnessSet.has(id)
                );

                let highlightBorder = 'border-zinc-800';
                let highlightBg = 'bg-zinc-900/40';
                let witnessBadge = null;

                if (isSupporting) {
                  highlightBorder = 'border-emerald-500/60 ring-1 ring-emerald-500/30';
                  highlightBg = 'bg-emerald-950/20';
                  witnessBadge = (
                    <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-300 border border-emerald-500/40">
                      SUPPORTING WITNESS
                    </span>
                  );
                } else if (isRefuting) {
                  highlightBorder = 'border-rose-500/60 ring-1 ring-rose-500/30';
                  highlightBg = 'bg-rose-950/20';
                  witnessBadge = (
                    <span className="rounded bg-rose-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-rose-300 border border-rose-500/40">
                      REFUTING WITNESS
                    </span>
                  );
                }

                // Distinguish direct declared evidence vs inferred correlation
                const hasDeclaredLink = evt.parents && evt.parents.length > 0;

                return (
                  <div
                    key={evt.event_id}
                    data-testid={`event-node-${evt.event_id}`}
                    tabIndex={0}
                    role="button"
                    aria-pressed={isSelected}
                    onClick={() => onSelectEvent?.(evt.event_id)}
                    onKeyDown={(e) => handleKeyDownSelect(e, evt.event_id)}
                    className={`cursor-pointer rounded-md border p-2.5 transition-all outline-none focus:ring-2 focus:ring-zinc-400 ${highlightBorder} ${highlightBg} ${
                      isSelected ? 'ring-2 ring-white/80' : 'hover:border-zinc-700'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span
                          className={`h-2 w-2 rounded-full ${
                            evt.status === 'ERROR' ? 'bg-rose-500' : 'bg-zinc-400'
                          }`}
                        />
                        <span className="font-mono text-xs font-medium text-zinc-200">
                          {evt.operation}
                        </span>
                        <span className="rounded bg-zinc-800 px-1.5 py-0.2 text-[10px] font-mono text-zinc-400">
                          {evt.kind}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        {witnessBadge}
                        <span
                          className={`text-[10px] font-mono px-1.5 py-0.2 rounded ${
                            hasDeclaredLink
                              ? 'border border-zinc-700 text-zinc-300'
                              : 'border border-dashed border-zinc-700 text-zinc-500'
                          }`}
                          title={
                            hasDeclaredLink
                              ? 'Direct declared evidence'
                              : 'Inferred correlation'
                          }
                        >
                          {hasDeclaredLink ? 'DECLARED' : 'INFERRED'}
                        </span>
                      </div>
                    </div>

                    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 text-[11px] font-mono text-zinc-400">
                      <span>{evt.actor || 'anonymous'}</span>
                      <span>{evt.time.start?.slice(11, 23) || 'no timestamp'}</span>
                      {evt.time.duration_ms !== null && (
                        <span>{evt.time.duration_ms.toFixed(1)}ms</span>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Column: Active Verdict, Missing-Fact Callout & Coverage Waterfall */}
        <div className="lg:col-span-5 space-y-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-zinc-400">
            Epistemic Assessment & Coverage
          </h3>

          {activeAssessment ? (
            <div
              data-testid={`assessment-card-${activeAssessment.contract}`}
              className="rounded-md border border-zinc-800 bg-zinc-900/50 p-3.5 space-y-3"
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold text-zinc-200">
                  {activeAssessment.contract}
                </span>
                {(() => {
                  const b =
                    VERDICT_BADGE_STYLES[activeAssessment.verdict] ||
                    VERDICT_BADGE_STYLES.inconclusive;
                  return (
                    <span
                      data-testid="assessment-verdict-badge"
                      className={`rounded px-2 py-0.5 text-xs font-bold border ${b.bg} ${b.text} ${b.border}`}
                    >
                      {b.label}
                    </span>
                  );
                })()}
              </div>

              <p className="text-xs text-zinc-300 leading-relaxed font-sans">
                {activeAssessment.summary}
              </p>

              {/* Inconclusive Missing-Fact Remedy Callout Box */}
              {activeAssessment.verdict === 'inconclusive' &&
                activeAssessment.missing_facts.length > 0 && (
                  <div
                    data-testid="missing-fact-callout"
                    className="rounded border border-amber-500/30 bg-amber-950/20 p-3 space-y-2.5"
                  >
                    <div className="flex items-center gap-1.5 text-amber-400 text-xs font-semibold">
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={2}
                          d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                        />
                      </svg>
                      Missing Telemetry & Observability Remedy
                    </div>

                    {activeAssessment.missing_facts.map((mf, i) => (
                      <div key={i} className="text-xs space-y-1 font-mono">
                        <div className="flex justify-between text-zinc-300">
                          <span>Reason: {mf.reason}</span>
                          <span className="text-amber-300/80">{mf.recoverability}</span>
                        </div>
                        <div className="text-zinc-400">
                          Target Field: <code className="text-zinc-200">{mf.field}</code>
                        </div>
                      </div>
                    ))}

                    {activeAssessment.remedies.map((remedy, i) => (
                      <div
                        key={i}
                        className="flex items-center justify-between rounded bg-zinc-900/80 p-2 text-xs font-mono text-zinc-300 border border-zinc-800"
                      >
                        <span className="truncate pr-2">{remedy}</span>
                        <button
                          data-testid="copy-remedy-button"
                          onClick={() => handleCopyRemedy(remedy)}
                          className="shrink-0 rounded bg-amber-500/20 px-2 py-1 text-[11px] font-sans font-medium text-amber-300 hover:bg-amber-500/30 transition-colors"
                        >
                          {copiedRemedy === remedy ? 'Copied!' : 'Copy Remedy'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
            </div>
          ) : (
            <div className="rounded border border-dashed border-zinc-800 p-4 text-center text-xs text-zinc-500 font-mono">
              No active contract assessment available.
            </div>
          )}

          {/* Coverage Waterfall */}
          <div className="rounded-md border border-zinc-800 bg-zinc-900/40 p-3.5 space-y-2.5">
            <h4 className="text-xs font-semibold text-zinc-300 uppercase tracking-wider">
              Sensor Completeness Waterfall
            </h4>

            {coverage.length === 0 ? (
              <p className="text-xs text-zinc-500 font-mono">
                No sensor coverage profiles attached.
              </p>
            ) : (
              <div className="space-y-2">
                {coverage.map((s) => (
                  <div
                    key={s.sensor_id}
                    data-testid={`sensor-row-${s.sensor_id}`}
                    className="rounded border border-zinc-800/80 bg-zinc-950/60 p-2 text-xs space-y-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5">
                        <span
                          className={`h-2 w-2 rounded-full ${
                            s.active ? 'bg-emerald-400' : 'bg-zinc-600'
                          }`}
                        />
                        <span className="font-mono text-zinc-200">{s.sensor_id}</span>
                      </div>
                      <span className="font-mono text-[11px] text-zinc-400">
                        {s.dropped_count > 0 ? (
                          <span className="text-rose-400 font-semibold">
                            {s.dropped_count} dropped
                          </span>
                        ) : (
                          <span className="text-emerald-400">0 drops (clean)</span>
                        )}
                      </span>
                    </div>

                    {s.gaps && s.gaps.length > 0 && (
                      <div className="rounded bg-rose-950/30 border border-rose-500/20 p-1.5 text-[11px] font-mono text-rose-300 space-y-0.5">
                        {s.gaps.map((g, gi) => (
                          <div key={gi}>
                            Sequence Gap: [{g.seq_lost_start}..{g.seq_lost_end}] ({g.count}{' '}
                            lost)
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
