import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useQueryClient } from '@tanstack/react-query';
import { Component, useEffect, useState, type FormEvent, type ReactNode } from 'react';

import { ApiError, useApiKey, useStatus } from '../../lib/protect/client';

export const SECURITY_VIEWS = [
  { href: '/security', label: 'OVERVIEW' },
  { href: '/security/agents', label: 'AGENTS' },
  { href: '/security/decisions', label: 'DECISIONS' },
  { href: '/security/approvals', label: 'APPROVALS' },
  { href: '/security/policy', label: 'POLICY' },
  { href: '/security/mcp', label: 'MCP' },
  { href: '/security/evidence', label: 'EVIDENCE' },
  { href: '/security/controls', label: 'CONTROLS' },
];

export function SecurityShell({ title, children }: { title: string; children: ReactNode }) {
  const router = useRouter();
  const [key, setKey] = useApiKey();
  const qc = useQueryClient();
  useEffect(() => {
    // signing out drops everything fetched with the old key, so the next key (another
    // principal) never sees it; queries only run while a key is set
    if (!key) qc.removeQueries({ queryKey: ['protect'] });
  }, [key, qc]);
  const status = useStatus();
  return (
    <div className="min-h-screen bg-[#08090c] text-zinc-200">
      <Head>
        <title>{`${title} · AgentWatch security`}</title>
      </Head>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:bg-zinc-800 focus:px-2 focus:py-1"
      >
        Skip to content
      </a>
      <nav
        aria-label="Security views"
        className="sticky top-0 z-20 flex flex-wrap items-center gap-1 border-b border-zinc-800 bg-[#08090c]/95 px-3 py-1.5"
      >
        <Link href="/" className="mr-3 font-mono text-sm font-semibold text-zinc-100">
          panoptis<span className="text-rose-400">/security</span>
        </Link>
        {SECURITY_VIEWS.map((v) => {
          const active =
            v.href === '/security'
              ? router.pathname === '/security'
              : router.pathname.startsWith(v.href);
          return (
            <Link
              key={v.href}
              href={v.href}
              aria-current={active ? 'page' : undefined}
              className={`rounded px-2 py-1 font-mono text-[11px] tracking-wider ${active ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-300 hover:text-zinc-50'}`}
            >
              {v.label}
            </Link>
          );
        })}
        <div className="ml-auto flex items-center gap-3">
          <Connection status={status} hasKey={!!key} />
          {key ? (
            <button
              type="button"
              onClick={() => setKey(null)}
              className="rounded border border-zinc-700 px-2 py-0.5 font-mono text-[11px] text-zinc-300 hover:text-zinc-50"
            >
              sign out
            </button>
          ) : null}
        </div>
      </nav>
      {status.data?.killed ? (
        <div
          role="alert"
          className="border-b border-rose-800 bg-rose-950/80 px-3 py-2 font-mono text-sm text-rose-200"
        >
          EMERGENCY STOP ACTIVE — every protected tool call is blocked.{' '}
          {status.data.kill_reason ? `Reason: ${status.data.kill_reason}` : ''}
        </div>
      ) : null}
      <main id="main" className="mx-auto max-w-[1400px] space-y-3 p-3">
        {key ? <ViewBoundary>{children}</ViewBoundary> : <SignIn onKey={setKey} />}
      </main>
    </div>
  );
}

function Connection({ status, hasKey }: { status: ReturnType<typeof useStatus>; hasKey: boolean }) {
  const now = useNow(5000);
  if (!hasKey) return <span className="font-mono text-[11px] text-zinc-400">not signed in</span>;
  if (status.isLoading)
    return <span className="font-mono text-[11px] text-zinc-400">connecting…</span>;
  const err = status.error as ApiError | null;
  if (err) {
    const text =
      err.status === 0
        ? 'disconnected'
        : err.status === 401
          ? 'key rejected'
          : `error ${err.status}`;
    return (
      <span role="status" className="font-mono text-[11px] text-rose-400">
        ● {text}
      </span>
    );
  }
  const age = status.dataUpdatedAt
    ? Math.max(0, Math.round((now - status.dataUpdatedAt) / 1000))
    : null;
  const stale = age !== null && age > 20;
  return (
    <span
      role="status"
      className={`font-mono text-[11px] ${stale ? 'text-amber-400' : 'text-emerald-400'}`}
      title="data refreshed from the AgentWatch API"
    >
      ● tenant {status.data?.tenant_id} · {stale ? `stale (${age}s)` : 'live'}
    </span>
  );
}

function SignIn({ onKey }: { onKey: (k: string) => void }) {
  const [value, setValue] = useState('');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (value.trim()) onKey(value.trim());
  };
  return (
    <form
      onSubmit={submit}
      className="mx-auto mt-16 max-w-md space-y-3 rounded border border-zinc-800 bg-zinc-950/70 p-5"
    >
      <h1 className="text-lg font-semibold text-zinc-100">Sign in to the security dashboard</h1>
      <p className="text-sm text-zinc-400">
        Use your AgentWatch API key. The server decides what it can see: a tenant key shows that
        tenant only; global controls need the operator key. The key is kept in this browser tab
        only.
      </p>
      <label htmlFor="api-key" className="block text-sm text-zinc-300">
        API key
      </label>
      <input
        id="api-key"
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-sm"
      />
      <button
        type="submit"
        className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-600"
      >
        Sign in
      </button>
    </form>
  );
}

/** A rendering error in one view shows an error state instead of a blank page. */
class ViewBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <p
          role="alert"
          className="rounded border border-rose-900 bg-rose-950/40 px-2 py-1 text-sm text-rose-300"
        >
          This view could not display the data it received ({this.state.error.message}). Nothing
          shown is stale or invented; reload to retry.
        </p>
      );
    }
    return this.props.children;
  }
}

/** The current time, refreshed every ``ms`` (render stays pure; the stale badge ages). */
function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
