import { useState } from 'react';

import { SecurityShell } from '../../components/security/SecurityShell';
import { Card, ErrorNote, QueryState, Time } from '../../components/security/ui';
import { useApprovals, useDecideApproval } from '../../lib/protect/client';
import type { Approval } from '../../lib/protect/types';

export default function Approvals() {
  const [state, setState] = useState('pending');
  const approvals = useApprovals(state === 'all' ? undefined : state);
  return (
    <SecurityShell title="Approvals">
      <h1 className="text-base font-semibold text-zinc-100">Human approvals</h1>
      <p className="text-sm text-zinc-400">
        Escalated tool calls wait here. An approval covers exactly the action shown (its digest);
        the tool runs once, only after it.
      </p>
      <label className="flex items-center gap-2 text-sm">
        show
        <select
          aria-label="approval state"
          className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          {['pending', 'approved', 'rejected', 'expired', 'cancelled', 'all'].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <QueryState
        q={approvals}
        isEmpty={!approvals.data?.approvals.length}
        empty={state === 'pending' ? 'No call is waiting for approval.' : 'No requests.'}
      >
        <div className="grid gap-2 md:grid-cols-2">
          {approvals.data?.approvals.map((a) => (
            <ApprovalCard key={a.request_id} a={a} />
          ))}
        </div>
      </QueryState>
    </SecurityShell>
  );
}

function ApprovalCard({ a }: { a: Approval }) {
  const decide = useDecideApproval();
  const [name, setName] = useState('');
  const [comment, setComment] = useState('');
  const pending = a.state === 'pending';
  const send = (approved: boolean) =>
    decide.mutate({
      request_id: a.request_id,
      approved,
      action_digest: a.action_digest,
      decided_by: name || 'dashboard',
      comment,
    });
  return (
    <Card title={`${a.tool} · ${a.state}`} right={<Time t={a.created_at} />}>
      <div className="space-y-2 text-sm" data-testid={`approval-${a.request_id}`}>
        <p>
          <span className="text-zinc-400">agent</span>{' '}
          <code className="font-mono">{a.agent_id}</code>{' '}
          <span className="text-zinc-400">session</span>{' '}
          <code className="font-mono">{a.session_id}</code>
        </p>
        <p>{a.reason}</p>
        <pre
          className="max-h-48 overflow-auto rounded border border-zinc-800 bg-zinc-900 p-2 font-mono text-[12px]"
          aria-label="arguments to approve"
        >
          {JSON.stringify(a.arguments, null, 2)}
        </pre>
        <p className="break-all font-mono text-[11px] text-zinc-400">digest {a.action_digest}</p>
        {pending ? (
          <>
            <p className="text-[12px] text-zinc-400">
              expires <Time t={a.expires_at} />
            </p>
            <div className="flex flex-wrap gap-2">
              <label className="flex items-center gap-1 text-[12px]">
                your name
                <input
                  aria-label="your name"
                  className="w-28 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label className="flex items-center gap-1 text-[12px]">
                comment
                <input
                  aria-label="comment"
                  className="w-40 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5"
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </label>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={decide.isPending}
                onClick={() => send(true)}
                className="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-600 disabled:opacity-50"
              >
                Approve
              </button>
              <button
                type="button"
                disabled={decide.isPending}
                onClick={() => send(false)}
                className="rounded bg-rose-700 px-3 py-1 text-sm text-white hover:bg-rose-600 disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          </>
        ) : (
          <p className="text-[12px] text-zinc-300">
            {String(a.outcome?.decided_by ?? '')}{' '}
            {a.outcome?.comment ? `— ${String(a.outcome.comment)}` : ''}
          </p>
        )}
        {decide.error ? <ErrorNote error={decide.error} /> : null}
        {decide.data && !decide.data.applied ? (
          <p className="text-[12px] text-amber-300">An earlier decision already stood.</p>
        ) : null}
      </div>
    </Card>
  );
}
