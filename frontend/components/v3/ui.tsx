import Link from 'next/link';
import type { ReactNode } from 'react';

export function Panel({
  title,
  right,
  children,
  className = '',
}: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`glass overflow-hidden ${className}`}>
      {title !== undefined && (
        <header className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-[11px] uppercase tracking-wider text-zinc-200">
          <span>{title}</span>
          {right}
        </header>
      )}
      <div className="p-3">{children}</div>
    </section>
  );
}

const STATUS: Record<string, string> = {
  OK: 'text-emerald-400',
  ERROR: 'text-rose-400',
  TIMEOUT: 'text-rose-400',
  CANCELLED: 'text-amber-400',
  UNKNOWN: 'text-zinc-400',
};

export const Status = ({ s }: { s?: string | null }) => (
  <span className={`font-mono text-xs ${STATUS[s ?? 'UNKNOWN'] ?? 'text-zinc-400'}`}>
    {s ?? '—'}
  </span>
);

const MATURITY: Record<string, string> = {
  EXPERIMENTAL: 'border-amber-600/60 text-amber-400',
  VALIDATED: 'border-sky-600/60 text-sky-400',
  PRODUCTION: 'border-emerald-600/60 text-emerald-400',
};

export const Maturity = ({ m }: { m?: string }) =>
  m ? (
    <span
      className={`rounded border px-1.5 py-px font-mono text-[10px] ${MATURITY[m] ?? 'border-zinc-600 text-zinc-400'}`}
    >
      {m}
    </span>
  ) : null;

export const Label = ({
  children,
  tone = 'zinc',
}: {
  children: ReactNode;
  tone?: 'zinc' | 'sky' | 'amber' | 'rose' | 'emerald' | 'violet';
}) => {
  const tones = {
    zinc: 'border-zinc-700 text-zinc-300',
    sky: 'border-sky-700 text-sky-300',
    amber: 'border-amber-700 text-amber-300',
    rose: 'border-rose-700 text-rose-300',
    emerald: 'border-emerald-700 text-emerald-300',
    violet: 'border-violet-700 text-violet-300',
  };
  return (
    <span className={`rounded border px-1.5 py-px font-mono text-[10px] ${tones[tone]}`}>
      {children}
    </span>
  );
};

export const short = (id?: string | null, n = 8) => (id ? id.slice(0, n) : '—');

export const fmtMs = (ms?: number | null) =>
  ms === null || ms === undefined
    ? '—'
    : ms < 1000
      ? `${ms.toFixed(1)} ms`
      : `${(ms / 1000).toFixed(2)} s`;

export const Mono = ({ children, className = '' }: { children: ReactNode; className?: string }) => (
  <span className={`font-mono text-xs ${className}`}>{children}</span>
);

export function Json({ value, max = 4000 }: { value: unknown; max?: number }) {
  const text = JSON.stringify(value, null, 2) ?? 'null';
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-all rounded bg-black/40 p-2 font-mono text-[11px] leading-snug text-zinc-300">
      {text.length > max ? text.slice(0, max) + '\n…' : text}
    </pre>
  );
}

export const Empty = ({ children }: { children: ReactNode }) => (
  <p className="py-6 text-center text-sm text-zinc-300">{children}</p>
);

export const ErrorBox = ({ error }: { error: unknown }) =>
  error ? (
    <p className="rounded-xl border border-rose-400/40 bg-rose-500/15 p-2 font-mono text-xs text-rose-100">
      {error instanceof Error ? error.message : String(error)}
    </p>
  ) : null;

export const RunLink = ({ id, view = 'timeline' }: { id: string; view?: string }) => (
  <Link className="font-mono text-xs text-sky-400 hover:underline" href={`/${view}?run=${id}`}>
    {short(id)}
  </Link>
);
