import { useState, type FormEvent } from 'react';

import { SecurityShell } from '../../components/security/SecurityShell';
import { Card, ErrorNote, QueryState, Table, Td, Verdict } from '../../components/security/ui';
import { usePermissions, usePolicy, useRbacCheck } from '../../lib/protect/client';

export default function PolicyPage() {
  const policy = usePolicy();
  const [agent, setAgent] = useState('');
  const [lookup, setLookup] = useState<string | undefined>();
  const perms = usePermissions(lookup);
  const check = useRbacCheck();
  const [tool, setTool] = useState('');
  const [args, setArgs] = useState('{}');
  const [argError, setArgError] = useState<string | null>(null);
  const runCheck = (e: FormEvent) => {
    e.preventDefault();
    try {
      const parsed = JSON.parse(args || '{}');
      setArgError(null);
      check.mutate({ agent_id: agent, tool, arguments: parsed });
    } catch {
      setArgError('Arguments must be a JSON object.');
    }
  };
  const p = policy.data;
  return (
    <SecurityShell title="Policy">
      <h1 className="text-base font-semibold text-zinc-100">RBAC policy</h1>
      <Card title="Active policy">
        <QueryState q={policy}>
          {p && !p.configured ? (
            <p className="text-sm text-amber-300">
              No policy is configured (AGENTWATCH_POLICY_FILE): tool authorization is not enforced.
            </p>
          ) : null}
          {p?.configured ? (
            <div className="space-y-2 text-sm">
              <p className="font-mono text-[12px] text-zinc-300">
                digest {p.digest} · version {p.version} · default <strong>{p.default}</strong>
              </p>
              {p.last_error ? (
                <ErrorNote error={new Error(`last reload rejected: ${p.last_error.join('; ')}`)} />
              ) : null}
              <Table label="roles" head={['role', 'allowed', 'forbidden', 'needs approval']}>
                {Object.entries(p.roles).map(([name, r]) => (
                  <tr key={name}>
                    <Td mono>{name}</Td>
                    <Td mono>{r.allowed_tools.join(', ') || '—'}</Td>
                    <Td mono>{r.forbidden_tools.join(', ') || '—'}</Td>
                    <Td mono>{r.require_approval.join(', ') || '—'}</Td>
                  </tr>
                ))}
              </Table>
              <Table label="agent bindings" head={['agent (glob)', 'roles']}>
                {Object.entries(p.agents).map(([a, roles]) => (
                  <tr key={a}>
                    <Td mono>{a}</Td>
                    <Td mono>{roles.join(', ')}</Td>
                  </tr>
                ))}
              </Table>
            </div>
          ) : null}
        </QueryState>
      </Card>
      <div className="grid gap-3 md:grid-cols-2">
        <Card title="Effective permissions">
          <form
            className="mb-2 flex gap-2 text-sm"
            onSubmit={(e) => {
              e.preventDefault();
              setLookup(agent || undefined);
            }}
          >
            <label htmlFor="perm-agent" className="sr-only">
              agent id
            </label>
            <input
              id="perm-agent"
              placeholder="agent id"
              className="rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-mono"
              value={agent}
              onChange={(e) => setAgent(e.target.value)}
            />
            <button type="submit" className="rounded border border-zinc-700 px-2 py-0.5">
              show
            </button>
          </form>
          {lookup ? (
            <QueryState q={perms}>
              <pre
                className="max-h-80 overflow-auto rounded border border-zinc-800 bg-zinc-900 p-2 font-mono text-[12px]"
                aria-label="effective permissions"
              >
                {JSON.stringify(perms.data, null, 2)}
              </pre>
            </QueryState>
          ) : null}
        </Card>
        <Card title="Would this call be allowed? (dry run)">
          <form className="space-y-2 text-sm" onSubmit={runCheck}>
            <div className="flex gap-2">
              <label className="flex items-center gap-1">
                agent
                <input
                  aria-label="check agent"
                  className="w-32 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 font-mono"
                  value={agent}
                  onChange={(e) => setAgent(e.target.value)}
                />
              </label>
              <label className="flex items-center gap-1">
                tool
                <input
                  aria-label="check tool"
                  className="w-32 rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 font-mono"
                  value={tool}
                  onChange={(e) => setTool(e.target.value)}
                />
              </label>
            </div>
            <label className="block">
              arguments (JSON)
              <textarea
                aria-label="check arguments"
                className="mt-1 h-20 w-full rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5 font-mono text-[12px]"
                value={args}
                onChange={(e) => setArgs(e.target.value)}
              />
            </label>
            <button
              type="submit"
              disabled={!agent || !tool}
              className="rounded border border-zinc-700 px-2 py-0.5 disabled:opacity-50"
            >
              check
            </button>
          </form>
          {argError ? (
            <p role="alert" className="text-sm text-rose-300">
              {argError}
            </p>
          ) : null}
          {check.error ? <ErrorNote error={check.error} /> : null}
          {check.data ? (
            <div className="mt-2 text-sm" aria-label="check result">
              <Verdict v={check.data.verdict} /> {check.data.authorized ? 'authorized' : 'denied'}
              <ul className="list-disc pl-5">
                {check.data.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>
      </div>
    </SecurityShell>
  );
}
