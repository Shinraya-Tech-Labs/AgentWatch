import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import { useQueryClient } from '@tanstack/react-query';
import { Component, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';

import { switchProfile, tryLocalSignIn, useProfiles } from '../../lib/local';
import { ApiError, useApiKey, useStatus } from '../../lib/protect/client';

export const PRIMARY_VIEWS = [
  { href: '/', label: 'Home' },
  { href: '/security/decisions', label: 'Decisions' },
  { href: '/security/approvals', label: 'Approvals' },
  { href: '/security/agents', label: 'Agents' },
  { href: '/security/evidence', label: 'Evidence' },
  { href: '/security/controls', label: 'Controls' },
];

export const ADVANCED_VIEWS = [
  { href: '/security/policy', label: 'Policy', hint: 'RBAC policy and dry-run checks' },
  { href: '/security/mcp', label: 'MCP & discovery', hint: 'MCP servers and local agents' },
  { href: '/live', label: 'Live activity', hint: 'raw observations as they arrive' },
  { href: '/map', label: 'Map', hint: 'execution, information and causal topology' },
  { href: '/timeline', label: 'Timeline', hint: 'execution history and evidence' },
  { href: '/lab', label: 'Lab', hint: 'replay, branching, counterfactuals' },
  { href: '/genome', label: 'Genome', hint: 'motifs, profiles, drift' },
  { href: '/compare', label: 'Compare', hint: 'runs and versions side by side' },
  { href: '/query', label: 'Query', hint: 'evidence-backed questions' },
];

/** Kept for older imports: every view, primary first. */
export const SECURITY_VIEWS = [...PRIMARY_VIEWS, ...ADVANCED_VIEWS];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/' || pathname === '/security';
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Brand() {
  return (
    <Link href="/" className="mr-2 flex shrink-0 items-center gap-2 text-zinc-50">
      <span
        aria-hidden
        className="grid h-7 w-7 place-items-center rounded-lg border border-white/20 bg-gradient-to-br from-sky-400/40 to-violet-500/40 text-[13px] font-bold"
      >
        P
      </span>
      <span className="text-sm font-semibold tracking-[0.18em]">PANOPTIS</span>
    </Link>
  );
}

function AdvancedMenu({ pathname }: { pathname: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = ADVANCED_VIEWS.some((v) => isActive(pathname, v.href));
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (
        e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)
      )
        setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
        className={`rounded-lg px-3 py-1.5 text-sm ${active ? 'bg-white/15 text-zinc-50' : 'text-zinc-300 hover:bg-white/10 hover:text-zinc-50'}`}
      >
        Advanced ▾
      </button>
      {open ? (
        <ul className="glass-strong absolute right-0 z-30 mt-2 w-72 p-1.5 md:left-0 md:right-auto">
          {ADVANCED_VIEWS.map((v) => (
            <li key={v.href}>
              <Link
                href={v.href}
                aria-current={isActive(pathname, v.href) ? 'page' : undefined}
                onClick={() => setOpen(false)}
                className="block rounded-lg px-3 py-2 hover:bg-white/10"
              >
                <span className="block text-sm text-zinc-50">{v.label}</span>
                <span className="block text-xs text-zinc-300">{v.hint}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function NavLinks({ pathname, onPick }: { pathname: string; onPick?: () => void }) {
  return (
    <>
      {PRIMARY_VIEWS.map((v) => {
        const active = isActive(pathname, v.href);
        return (
          <Link
            key={v.href}
            href={v.href}
            onClick={onPick}
            aria-current={active ? 'page' : undefined}
            className={`rounded-lg px-3 py-1.5 text-sm ${active ? 'bg-white/15 text-zinc-50' : 'text-zinc-300 hover:bg-white/10 hover:text-zinc-50'}`}
          >
            {v.label}
          </Link>
        );
      })}
    </>
  );
}

function ProfileSwitch({ className = '' }: { className?: string }) {
  const { profiles, active } = useProfiles();
  if (profiles.length < 2) return null;
  return (
    <label className={`items-center gap-1.5 text-xs text-zinc-300 ${className}`}>
      <span className="sr-only">Viewing</span>
      <select
        aria-label="viewing"
        value={active?.id ?? ''}
        onChange={(e) => switchProfile(e.target.value)}
        className="glass-input px-2 py-1 text-xs"
      >
        {!active ? <option value="">signed in with a key</option> : null}
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** The page frame shared by every view: header, banners and main area (no sign-in gate). */
export function Chrome({
  title,
  children,
  toolbar,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  toolbar?: ReactNode;
  wide?: boolean;
}) {
  const router = useRouter();
  const pathname = router?.pathname ?? '/';
  const [key, setKey] = useApiKey();
  const [menu, setMenu] = useState(false);
  const status = useStatus();
  const { active } = useProfiles();
  return (
    <div className="min-h-screen text-zinc-100">
      <Head>
        <title>{`${title} · PANOPTIS`}</title>
      </Head>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-zinc-900 focus:px-2 focus:py-1"
      >
        Skip to content
      </a>
      <header className="glass-bar sticky top-0 z-20">
        <nav
          aria-label="Dashboard views"
          className="mx-auto flex max-w-[1400px] items-center gap-1 px-4 py-2"
        >
          <Brand />
          <div className="hidden items-center gap-0.5 md:flex">
            <NavLinks pathname={pathname} />
            <AdvancedMenu pathname={pathname} />
          </div>
          <div className="ml-auto flex items-center gap-2">
            {toolbar}
            <ProfileSwitch className="hidden md:flex" />
            <Connection status={status} hasKey={!!key} />
            {key && !active ? (
              <button
                type="button"
                onClick={() => setKey(null)}
                className="hidden rounded-lg border border-white/15 px-2 py-1 text-xs text-zinc-200 hover:bg-white/10 sm:block"
              >
                sign out
              </button>
            ) : null}
            <button
              type="button"
              className="rounded-lg border border-white/15 px-2.5 py-1 text-sm md:hidden"
              aria-expanded={menu}
              aria-controls="mobile-nav"
              onClick={() => setMenu((m) => !m)}
            >
              Menu
            </button>
          </div>
        </nav>
        {menu ? (
          <div id="mobile-nav" className="border-t border-white/10 px-4 pb-3 md:hidden">
            <div className="flex flex-col gap-0.5 pt-2">
              <ProfileSwitch className="flex px-3 pb-2" />
              <NavLinks pathname={pathname} onPick={() => setMenu(false)} />
              <p className="px-3 pt-3 text-xs uppercase tracking-wider text-zinc-400">Advanced</p>
              {ADVANCED_VIEWS.map((v) => (
                <Link
                  key={v.href}
                  href={v.href}
                  onClick={() => setMenu(false)}
                  className="rounded-lg px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/10"
                >
                  {v.label}
                </Link>
              ))}
            </div>
          </div>
        ) : null}
      </header>
      {status.data?.killed ? (
        <div
          role="alert"
          className="border-b border-rose-400/40 bg-rose-600/30 px-4 py-2.5 text-center text-sm font-medium text-rose-50 backdrop-blur"
        >
          EMERGENCY STOP ACTIVE — every protected tool call is blocked.{' '}
          {status.data.kill_reason ? `Reason: ${status.data.kill_reason}` : ''}
        </div>
      ) : null}
      {active?.sample_data ? (
        <div
          role="note"
          className="border-b border-amber-300/30 bg-amber-400/15 px-4 py-2 text-center text-sm text-amber-50"
        >
          <strong>Demo workspace</strong>: sample data from a scripted agent, kept apart from your
          own workspace. Remove it with <code>panoptis demo --clear</code>.
        </div>
      ) : null}
      <main
        id="main"
        className={`mx-auto space-y-4 px-4 py-5 ${wide ? 'max-w-[1600px]' : 'max-w-[1400px]'}`}
      >
        {children}
      </main>
    </div>
  );
}

/** Security views: the frame plus sign-in (automatic in local mode). */
export function SecurityShell({ title, children }: { title: string; children: ReactNode }) {
  const [key, setKey] = useApiKey();
  const qc = useQueryClient();
  useEffect(() => {
    // signing out drops everything fetched with the old key, so the next key (another
    // principal) never sees it; queries only run while a key is set
    if (!key) qc.removeQueries({ queryKey: ['protect'] });
  }, [key, qc]);
  useEffect(() => {
    if (!key) void tryLocalSignIn();
  }, [key]);
  return (
    <Chrome title={title}>
      {key ? <ViewBoundary>{children}</ViewBoundary> : <SignIn onKey={setKey} />}
    </Chrome>
  );
}

function Dot({ tone }: { tone: string }) {
  return <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${tone}`} />;
}

function Connection({ status, hasKey }: { status: ReturnType<typeof useStatus>; hasKey: boolean }) {
  const now = useNow(5000);
  if (!hasKey) return <span className="pill text-zinc-300">not signed in</span>;
  if (status.isLoading) return <span className="pill text-zinc-300">connecting…</span>;
  const err = status.error as ApiError | null;
  if (err) {
    const text =
      err.status === 0
        ? 'disconnected'
        : err.status === 401
          ? 'key rejected'
          : `error ${err.status}`;
    return (
      <span role="status" className="pill border-rose-400/40 text-rose-200">
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
      className={`pill ${stale ? 'border-amber-300/40 text-amber-100' : 'border-emerald-300/40 text-emerald-100'}`}
      title="data refreshed from the PANOPTIS API"
    >
      <Dot tone={stale ? 'bg-amber-300' : 'bg-emerald-300 shadow-[0_0_8px_#6ee7b7]'} />
      <span className="hidden sm:inline">
        tenant {status.data?.tenant_id} · {stale ? `stale (${age}s)` : 'live'}
      </span>
      <span className="sm:hidden">{stale ? 'stale' : 'live'}</span>
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
    <form onSubmit={submit} className="glass-strong mx-auto mt-16 max-w-md space-y-4 p-6">
      <h1 className="text-xl font-semibold text-zinc-50">Sign in to PANOPTIS</h1>
      <p className="text-sm leading-relaxed text-zinc-300">
        Use your PANOPTIS API key. The server decides what it can see: a tenant key shows that
        tenant only; global controls need the operator key. The key is kept in this browser tab
        only. Running <code className="text-zinc-100">panoptis up</code> on this machine signs you
        in automatically.
      </p>
      <label htmlFor="api-key" className="block text-sm text-zinc-200">
        API key
      </label>
      <input
        id="api-key"
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="glass-input w-full px-3 py-2 font-mono text-sm"
      />
      <button
        type="submit"
        className="rounded-lg bg-sky-500 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-sky-400"
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
          className="rounded-xl border border-rose-400/40 bg-rose-500/15 px-3 py-2 text-sm text-rose-100"
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
