import type { ReactNode } from 'react';

import { ApiError } from '../../lib/protect/client';

export function Card({
  title,
  right,
  children,
}: {
  title: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={typeof title === 'string' ? title : undefined}
      className="glass overflow-hidden"
    >
      <header className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-2.5">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-200">{title}</h2>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Loading, error (by kind), or empty; renders children only with data. */
export function QueryState({
  q,
  empty,
  isEmpty,
  children,
}: {
  q: { isLoading: boolean; error: unknown; data?: unknown };
  empty?: ReactNode;
  isEmpty?: boolean;
  children: ReactNode;
}) {
  if (q.isLoading)
    return (
      <p className="text-sm text-zinc-300" role="status">
        Loading…
      </p>
    );
  if (q.error) return <ErrorNote error={q.error} />;
  if (isEmpty) return <div className="text-sm text-zinc-300">{empty ?? 'Nothing yet.'}</div>;
  return <>{children}</>;
}

export function ErrorNote({ error }: { error: unknown }) {
  const e = error as ApiError;
  const text =
    e?.status === 0
      ? 'Disconnected: the PANOPTIS API cannot be reached. Showing nothing rather than stale data.'
      : e?.status === 401
        ? 'Your key was not accepted. Sign out and sign in again.'
        : e?.status === 403
          ? 'Not permitted for this key (operator only).'
          : e?.status === 404
            ? 'Not found for this tenant.'
            : `Error ${e?.status ?? ''}: ${e?.message ?? String(error)}`;
  return (
    <p
      role="alert"
      className="rounded-xl border border-rose-400/40 bg-rose-500/15 px-3 py-2 text-sm text-rose-100"
    >
      {text}
    </p>
  );
}

const VERDICT: Record<string, string> = {
  allow: 'border-emerald-300/40 bg-emerald-400/10 text-emerald-200',
  block: 'border-rose-300/50 bg-rose-500/15 text-rose-200',
  escalate_hitl: 'border-amber-300/50 bg-amber-400/15 text-amber-100',
  mutate_and_allow: 'border-sky-300/40 bg-sky-400/10 text-sky-200',
};

export const Verdict = ({ v }: { v: string }) => (
  <span
    className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${VERDICT[v] ?? 'border-white/20 text-zinc-200'}`}
  >
    {v}
  </span>
);

const MONITOR: Record<string, string> = {
  MONITORED: 'border-emerald-700 text-emerald-300',
  UNMONITORED: 'border-rose-700 text-rose-300',
  UNKNOWN: 'border-amber-700 text-amber-300',
  UNSUPPORTED: 'border-fuchsia-700 text-fuchsia-300',
};

/** Only MONITORED is green: detection is never shown as protection. */
export const MonitorStatus = ({ s }: { s: string }) => (
  <span
    className={`rounded border px-1.5 py-px font-mono text-[11px] ${MONITOR[s] ?? 'border-zinc-600 text-zinc-300'}`}
  >
    {s}
  </span>
);

export const Time = ({ t }: { t?: string | number | null }) => {
  if (t === null || t === undefined || t === '') return <span className="text-zinc-400">—</span>;
  const d = typeof t === 'number' ? new Date(t * 1000) : new Date(t);
  return (
    <time dateTime={d.toISOString()} className="font-mono text-[11px] text-zinc-300">
      {d.toISOString().slice(0, 19).replace('T', ' ')}
    </time>
  );
};

export function Table({
  head,
  children,
  label,
}: {
  head: string[];
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm" aria-label={label}>
        <thead>
          <tr className="border-b border-white/10 text-[11px] uppercase tracking-wider text-zinc-300">
            {head.map((h) => (
              <th key={h} scope="col" className="px-2 py-1 font-normal">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export const Td = ({ children, mono = false }: { children: ReactNode; mono?: boolean }) => (
  <td
    className={`border-b border-white/5 px-2 py-1.5 align-top ${mono ? 'font-mono text-[12px]' : ''}`}
  >
    {children}
  </td>
);

const TONE = {
  zinc: 'text-zinc-50',
  rose: 'text-rose-200',
  amber: 'text-amber-100',
  emerald: 'text-emerald-200',
  sky: 'text-sky-200',
};

export const Stat = ({
  label,
  value,
  tone = 'zinc',
}: {
  label: string;
  value: ReactNode;
  tone?: keyof typeof TONE;
}) => (
  <div className="glass px-4 py-3">
    <div className="text-[11px] uppercase tracking-wider text-zinc-300">{label}</div>
    <div className={`mt-0.5 font-mono text-2xl font-semibold ${TONE[tone]}`}>{value}</div>
  </div>
);
