import Link from 'next/link';
import { useState } from 'react';

import {
  useApprovals,
  useControl,
  useControlAction,
  useDecideApproval,
  useDecisions,
  useStatus,
} from '../../lib/protect/client';
import type { Approval, Decision } from '../../lib/protect/types';
import { GettingStarted } from './GettingStarted';
import { SecurityShell } from './SecurityShell';
import { Card, ErrorNote, QueryState, Time } from './ui';

const PLAIN: Record<string, { word: string; tone: string; icon: string }> = {
  allow: { word: 'Allowed', tone: 'text-emerald-200', icon: '✓' },
  block: { word: 'Blocked', tone: 'text-rose-200', icon: '✕' },
  // an escalation is only "held" when it is in the approval queue (approval_state pending)
  escalate_hitl: { word: 'Needed approval, not held', tone: 'text-amber-100', icon: '!' },
  mutate_and_allow: { word: 'Allowed after rewriting', tone: 'text-sky-200', icon: '✎' },
};

export function verdictWord(d: Decision): { word: string; tone: string; icon: string } {
  const s = d.approval_state;
  if (s === 'pending') return { word: 'Held for approval', tone: 'text-amber-100', icon: '⏸' };
  if (s === 'approved')
    return { word: 'Approved by a person', tone: 'text-emerald-200', icon: '✓' };
  if (s === 'rejected') return { word: 'Rejected by a person', tone: 'text-rose-200', icon: '✕' };
  if (s === 'expired')
    return { word: 'Approval timed out, blocked', tone: 'text-rose-200', icon: '✕' };
  if (s === 'cancelled')
    return { word: 'Approval withdrawn, blocked', tone: 'text-rose-200', icon: '✕' };
  if (s === 'handed_to_host')
    return { word: "Handed to the agent's own prompt", tone: 'text-amber-100', icon: '↪' };
  if (s === 'unavailable' && d.verdict === 'block')
    return { word: 'Needed approval, no approver: blocked', tone: 'text-rose-200', icon: '✕' };
  return PLAIN[d.verdict] ?? { word: d.verdict, tone: 'text-zinc-100', icon: '•' };
}

function ago(at?: string | number | null): string {
  if (at === null || at === undefined || at === '') return '';
  const t = typeof at === 'number' ? at * 1000 : Date.parse(at);
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

type Answer = { title: string; detail: string; tone: 'ok' | 'warn' | 'bad' | 'idle' };

function answer(
  s: ReturnType<typeof useStatus>['data'],
  pending: number,
  recentBlocks: number,
): Answer {
  if (!s) return { title: 'Checking…', detail: '', tone: 'idle' };
  if (s.killed)
    return {
      title: 'Emergency stop is on',
      detail: 'Every protected tool call is refused until an operator lifts it.',
      tone: 'bad',
    };
  if (!s.recent_decisions)
    return {
      title: 'No protected agent has reported yet',
      detail:
        'Nothing to judge yet. Wrap your agent’s tools with PANOPTIS, or run `panoptis demo live`, and its calls appear here as they happen.',
      tone: 'idle',
    };
  if (pending)
    return {
      title: `${pending} call${pending === 1 ? '' : 's'} waiting for you`,
      detail: 'An agent is paused until a person approves or rejects. Nothing ran yet.',
      tone: 'warn',
    };
  if (recentBlocks)
    return {
      title: 'Protected: risky calls were stopped',
      detail: `${recentBlocks} recent call${recentBlocks === 1 ? ' was' : 's were'} blocked before running. Review them below.`,
      tone: 'ok',
    };
  return {
    title: 'Protected: nothing risky recently',
    detail: 'Every recent call routed through PANOPTIS was allowed.',
    tone: 'ok',
  };
}

const ANSWER_TONE: Record<Answer['tone'], string> = {
  ok: 'from-emerald-400/25 to-sky-400/10 border-emerald-300/30',
  warn: 'from-amber-400/25 to-orange-400/10 border-amber-300/40',
  bad: 'from-rose-500/35 to-rose-500/10 border-rose-300/50',
  idle: 'from-white/10 to-white/0 border-white/15',
};

export default function Home() {
  const status = useStatus();
  const recent = useDecisions({ limit: 25 });
  const approvals = useApprovals('pending');
  const s = status.data;
  const pending = approvals.data?.approvals.length ?? 0;
  const list = recent.data?.decisions ?? [];
  const a = answer(s, pending, s?.verdicts.block ?? 0);
  // a workspace nothing has reported to yet: show how to connect an agent instead of zeros
  if (s && !s.killed && !s.recent_decisions && approvals.data && !pending) {
    return (
      <SecurityShell title="Getting started">
        <GettingStarted />
      </SecurityShell>
    );
  }
  return (
    <SecurityShell title="Home">
      <section
        aria-labelledby="safe-q"
        className={`glass-strong bg-gradient-to-br p-5 sm:p-7 ${ANSWER_TONE[a.tone]}`}
      >
        <p id="safe-q" className="text-xs font-semibold uppercase tracking-[0.2em] text-zinc-200">
          Is my agent safe?
        </p>
        <QueryState q={status}>
          <h1 className="mt-2 text-2xl font-semibold text-zinc-50 sm:text-3xl">{a.title}</h1>
          {a.detail ? (
            <p className="mt-1.5 max-w-3xl text-sm text-zinc-200 sm:text-base">{a.detail}</p>
          ) : null}
        </QueryState>
        {s ? (
          <dl className="mt-5 grid grid-cols-3 gap-2 sm:max-w-xl sm:gap-3">
            <Count label="allowed" value={s.verdicts.allow ?? 0} tone="text-emerald-200" />
            <Count label="blocked" value={s.verdicts.block ?? 0} tone="text-rose-200" />
            <Count
              label="waiting for you"
              value={approvals.data ? pending : '—'}
              tone="text-amber-100"
              href="/security/approvals"
            />
          </dl>
        ) : null}
        {s ? (
          <p className="mt-2 text-xs text-zinc-300">
            Counts cover the latest {s.recent_decisions} decision
            {s.recent_decisions === 1 ? '' : 's'}
            {(s.verdicts.mutate_and_allow ?? 0) > 0
              ? ` (${s.verdicts.mutate_and_allow} allowed after rewriting)`
              : ''}
            . Last decision <Time t={s.last_decision_at} />.
          </p>
        ) : null}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card
            title="Live decisions"
            right={
              <Link className="text-xs text-sky-200 underline" href="/security/decisions">
                all decisions
              </Link>
            }
          >
            <QueryState
              q={recent}
              isEmpty={!list.length}
              empty={
                <div className="space-y-2">
                  <p>No protected tool calls yet.</p>
                  <p className="text-zinc-400">
                    Calls appear here the moment an agent wrapped with PANOPTIS proposes them.
                    Nothing is shown until something real happens.
                  </p>
                </div>
              }
            >
              <ol aria-label="recent decisions" className="divide-y divide-white/5">
                {list.map((d) => (
                  <FeedItem key={d.obs_id} d={d} />
                ))}
              </ol>
            </QueryState>
          </Card>
        </div>
        <div className="space-y-4">
          <EmergencyStop />
          <Card
            title={`Waiting for approval${pending ? ` (${pending})` : ''}`}
            right={
              <Link className="text-xs text-sky-200 underline" href="/security/approvals">
                all
              </Link>
            }
          >
            <QueryState q={approvals} isEmpty={!pending} empty="No call is waiting for approval.">
              <ul className="space-y-3">
                {approvals.data?.approvals.slice(0, 4).map((x) => (
                  <PendingApproval key={x.request_id} a={x} />
                ))}
              </ul>
            </QueryState>
          </Card>
          {s ? (
            <Card title="Setup">
              <ul className="space-y-2 text-sm">
                <li>
                  {s.policy.configured ? (
                    <span>
                      RBAC policy active ·{' '}
                      <Link className="text-sky-200 underline" href="/security/policy">
                        view
                      </Link>
                    </span>
                  ) : (
                    <span className="text-amber-100">
                      No RBAC policy is configured: tool authorization is not enforced.
                    </span>
                  )}
                </li>
                <li>
                  {s.evidence.head ? (
                    <span>
                      Evidence: {s.evidence.segments} sealed segment
                      {s.evidence.segments === 1 ? '' : 's'} ·{' '}
                      <Link className="text-sky-200 underline" href="/security/evidence">
                        verify
                      </Link>
                    </span>
                  ) : (
                    <span className="text-zinc-300">No sealed evidence yet.</span>
                  )}
                </li>
                <li className="text-zinc-300">
                  Suspended agents / sessions: {s.suspended_agents} / {s.suspended_sessions}
                </li>
              </ul>
            </Card>
          ) : null}
        </div>
      </div>
    </SecurityShell>
  );
}

function Count({
  label,
  value,
  tone,
  href,
}: {
  label: string;
  value: number | string;
  tone: string;
  href?: string;
}) {
  const num = <span className={`font-mono text-3xl font-semibold ${tone}`}>{value}</span>;
  return (
    <div className={`glass px-3 py-2.5 ${href ? 'hover:bg-white/10' : ''}`}>
      <dt className="text-[11px] uppercase tracking-wider text-zinc-200">{label}</dt>
      <dd>
        {href ? (
          <Link href={href} aria-label={`${value} ${label}: open approvals`}>
            {num}
          </Link>
        ) : (
          num
        )}
      </dd>
    </div>
  );
}

function FeedItem({ d }: { d: Decision }) {
  const v = verdictWord(d);
  const what = d.action_preview ? (
    <code className="break-all rounded bg-black/30 px-1.5 py-0.5 font-mono text-[12px] text-zinc-100">
      {d.action_preview}
    </code>
  ) : null;
  return (
    <li className="flex gap-3 py-3">
      <span aria-hidden className={`mt-0.5 w-5 shrink-0 text-center text-base ${v.tone}`}>
        {v.icon}
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm text-zinc-100">
          <span className={`font-semibold ${v.tone}`}>{v.word}:</span>{' '}
          <span className="font-mono text-zinc-200">{d.agent_id || 'an agent'}</span> wanted to use{' '}
          <span className="font-mono text-zinc-200">{d.tool}</span> {what}
        </p>
        {d.verdict !== 'allow' && d.reason ? (
          <p className="text-[13px] text-zinc-300">{d.reason}</p>
        ) : null}
      </div>
      <span className="shrink-0 text-xs text-zinc-400" title={String(d.at ?? '')}>
        {ago(d.at)}
      </span>
    </li>
  );
}

function PendingApproval({ a }: { a: Approval }) {
  const decide = useDecideApproval();
  const send = (approved: boolean) =>
    decide.mutate({
      request_id: a.request_id,
      approved,
      action_digest: a.action_digest,
      decided_by: 'dashboard',
    });
  const shown =
    typeof a.arguments?.command === 'string' ? a.arguments.command : JSON.stringify(a.arguments);
  return (
    <li className="rounded-xl border border-amber-300/30 bg-amber-400/10 p-3">
      <p className="text-sm text-zinc-100">
        <span className="font-mono">{a.agent_id}</span> wants to use{' '}
        <span className="font-mono">{a.tool}</span>
      </p>
      <code className="mt-1 block break-all font-mono text-[12px] text-amber-50">{shown}</code>
      <p className="mt-1 text-xs text-zinc-300">{a.reason}</p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          disabled={decide.isPending}
          onClick={() => send(true)}
          className="rounded-lg bg-emerald-400 px-3 py-1 text-sm font-semibold text-zinc-950 hover:bg-emerald-300 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={decide.isPending}
          onClick={() => send(false)}
          className="rounded-lg border border-rose-300/50 px-3 py-1 text-sm text-rose-100 hover:bg-rose-500/20 disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {decide.error ? <ErrorNote error={decide.error} /> : null}
    </li>
  );
}

function EmergencyStop() {
  const control = useControl();
  const action = useControlAction();
  const [confirm, setConfirm] = useState(false);
  const [reason, setReason] = useState('');
  const c = control.data;
  const run = (path: string) => {
    action.mutate({ path, reason: reason.trim() || 'emergency stop from the home screen' });
    setConfirm(false);
  };
  return (
    <section
      aria-label="Emergency stop"
      className={`glass-strong p-4 ${c?.killed ? 'border-rose-300/60' : ''}`}
    >
      <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">
        Emergency stop
      </h2>
      <p className="mt-1 text-sm text-zinc-300">
        Refuses every protected tool call before it runs, on every agent using this store. Running
        tools finish; pending approvals are withdrawn.
      </p>
      {c?.killed ? (
        <button
          type="button"
          disabled={action.isPending}
          onClick={() => run('/reset')}
          className="mt-3 w-full rounded-xl bg-emerald-400 px-4 py-3 text-base font-semibold text-zinc-950 hover:bg-emerald-300 disabled:opacity-50"
        >
          Lift emergency stop
        </button>
      ) : confirm ? (
        <div className="mt-3 space-y-2">
          <input
            aria-label="stop reason"
            placeholder="reason (recorded)"
            className="glass-input w-full px-3 py-2 text-sm"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={action.isPending}
              onClick={() => run('/kill')}
              className="flex-1 rounded-xl bg-rose-600 px-4 py-2.5 font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
            >
              Stop all agents now
            </button>
            <button
              type="button"
              onClick={() => setConfirm(false)}
              className="rounded-xl border border-white/20 px-3 py-2.5 text-sm"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          disabled={!c}
          onClick={() => setConfirm(true)}
          className="mt-3 w-full rounded-xl bg-rose-600 px-4 py-3 text-base font-bold tracking-wide text-white shadow-[0_0_30px_-6px_rgba(244,63,94,0.8)] hover:bg-rose-500 disabled:opacity-50"
        >
          ■ EMERGENCY STOP
        </button>
      )}
      {action.error ? (
        <div className="mt-2">
          <ErrorNote error={action.error} />
        </div>
      ) : null}
      {control.error ? (
        <div className="mt-2">
          <ErrorNote error={control.error} />
        </div>
      ) : null}
    </section>
  );
}
