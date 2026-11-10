import { useRouter } from 'next/router';
import { useEffect, type ReactNode } from 'react';

import { tryLocalSignIn } from '../../lib/local';
import { useApiKey } from '../../lib/protect/client';
import { useRuns } from '../../lib/v3/client';
import { ADVANCED_VIEWS, Chrome } from '../security/SecurityShell';
import { short } from './ui';

/** The Advanced (Evidence Lab) views, listed under "Advanced" in the main navigation. */
export const VIEWS = ADVANCED_VIEWS;

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
  const [key] = useApiKey();
  useEffect(() => {
    if (!key) void tryLocalSignIn();
  }, [key]);
  const runs = useRuns();
  const current = typeof router.query.run === 'string' ? router.query.run : undefined;
  const setRun = (id: string) =>
    router.push({ pathname: router.pathname, query: { ...router.query, run: id } }, undefined, {
      shallow: true,
    });
  const picker = runPicker ? (
    <select
      aria-label="run"
      className="glass-input hidden max-w-[300px] px-2 py-1 font-mono text-[11px] lg:block"
      value={runs.data?.runs.find((r) => current && r.run_id.startsWith(current))?.run_id ?? ''}
      onChange={(e) => setRun(e.target.value)}
    >
      <option value="">select run…</option>
      {runs.data?.runs.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {short(r.run_id)} · {r.name} · {r.status} · {r.started_at?.slice(5, 19).replace('T', ' ')}
        </option>
      ))}
    </select>
  ) : null;
  return (
    <Chrome title={title} toolbar={picker} wide>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-zinc-50">
          <span className="text-zinc-300">Advanced ·</span> {title.toLowerCase()}
        </h1>
        <span className="font-mono text-[10px] text-zinc-400" title="interpretation id">
          {runs.data?.interp_id ?? ''}
        </span>
      </div>
      {runPicker ? (
        <div className="lg:hidden">{picker && <MobilePicker>{picker}</MobilePicker>}</div>
      ) : null}
      {children}
    </Chrome>
  );
}

function MobilePicker({ children }: { children: ReactNode }) {
  // the toolbar copy is hidden below lg; show the same control in the page flow
  return <div className="[&>select]:block [&>select]:w-full">{children}</div>;
}
