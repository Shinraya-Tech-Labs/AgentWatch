import Head from 'next/head';
import Link from 'next/link';
import { useRouter } from 'next/router';
import type { ReactNode } from 'react';

import { useRuns } from '../../lib/v3/client';
import { short } from './ui';

export const VIEWS = [
  { href: '/', label: 'LIVE', hint: 'current computational activity' },
  { href: '/map', label: 'MAP', hint: 'execution / information / causal topology' },
  { href: '/timeline', label: 'TIMELINE', hint: 'execution history and evidence' },
  { href: '/lab', label: 'LAB', hint: 'replay, branching, counterfactuals' },
  { href: '/genome', label: 'GENOME', hint: 'motifs, profiles, drift' },
  { href: '/compare', label: 'COMPARE', hint: 'runs and versions side by side' },
  { href: '/query', label: 'QUERY', hint: 'evidence-backed questions' },
  {
    href: '/security',
    label: 'SECURITY',
    hint: 'runtime protection, approvals, evidence, emergency stop',
  },
];

export function Shell({
  title,
  children,
  runPicker = false,
}: {
  title: string;
  children: ReactNode;
  runPicker?: boolean;
}) {
  const router = useRouter();
  const runs = useRuns();
  const current = typeof router.query.run === 'string' ? router.query.run : undefined;
  const setRun = (id: string) =>
    router.push({ pathname: router.pathname, query: { ...router.query, run: id } }, undefined, {
      shallow: true,
    });
  return (
    <div className="min-h-screen bg-[#08090c] text-zinc-200">
      <Head>
        <title>{`${title} · AgentWatch`}</title>
      </Head>
      <nav className="sticky top-0 z-20 flex items-center gap-1 border-b border-zinc-800 bg-[#08090c]/95 px-3 py-1.5 backdrop-blur">
        <span className="mr-3 font-mono text-sm font-semibold tracking-tight text-zinc-100">
          panoptis<span className="text-sky-400">/v3</span>
        </span>
        {VIEWS.map((v) => {
          const active =
            v.href === '/' ? router.pathname === '/' : router.pathname.startsWith(v.href);
          return (
            <Link
              key={v.href}
              href={{ pathname: v.href, query: current ? { run: current } : {} }}
              title={v.hint}
              className={`rounded px-2 py-1 font-mono text-[11px] tracking-wider ${active ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-400 hover:text-zinc-100'}`}
            >
              {v.label}
            </Link>
          );
        })}
        <div className="ml-auto flex items-center gap-2">
          {runPicker && (
            <select
              aria-label="run"
              className="max-w-[340px] rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono text-[11px]"
              value={
                runs.data?.runs.find((r) => current && r.run_id.startsWith(current))?.run_id ?? ''
              }
              onChange={(e) => setRun(e.target.value)}
            >
              <option value="">select run…</option>
              {runs.data?.runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {short(r.run_id)} · {r.name} · {r.status} ·{' '}
                  {r.started_at?.slice(5, 19).replace('T', ' ')}
                </option>
              ))}
            </select>
          )}
          <span className="font-mono text-[10px] text-zinc-500" title="interpretation id">
            {runs.data?.interp_id ?? ''}
          </span>
        </div>
      </nav>
      <main className="mx-auto max-w-[1600px] p-3">{children}</main>
    </div>
  );
}
