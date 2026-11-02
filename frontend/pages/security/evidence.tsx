import { useState } from 'react';

import { SecurityShell } from '../../components/security/SecurityShell';
import { Card, ErrorNote, QueryState } from '../../components/security/ui';
import { useEvidence, useProof } from '../../lib/protect/client';

export default function EvidencePage() {
  const [nonce, setNonce] = useState(0);
  const [seal, setSeal] = useState(false);
  const report = useEvidence(seal, nonce);
  const [obs, setObs] = useState('');
  const [proofId, setProofId] = useState<string | undefined>();
  const proof = useProof(proofId);
  const r = report.data;
  return (
    <SecurityShell title="Evidence">
      <h1 className="text-base font-semibold text-zinc-100">Evidence integrity</h1>
      <Card
        title="Merkle chain verification"
        right={
          <div className="flex items-center gap-2 text-[12px]">
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={seal} onChange={(e) => setSeal(e.target.checked)} />{' '}
              seal pending first
            </label>
            <button
              type="button"
              className="rounded border border-zinc-700 px-2 py-0.5"
              onClick={() => setNonce((n) => n + 1)}
            >
              verify now
            </button>
          </div>
        }
      >
        <QueryState q={report}>
          {r ? (
            <div className="space-y-2 text-sm" aria-label="verification result">
              <p className={r.ok ? 'text-emerald-300' : 'text-rose-300'} role="status">
                {r.ok
                  ? 'VERIFIED — the chain, Merkle roots and records are intact.'
                  : 'FAILED — evidence was modified or is incomplete.'}
              </p>
              <p className="font-mono text-[12px] text-zinc-300">
                {r.segments_checked} segments · {r.observations_checked} records ·{' '}
                {r.unsealed_observations} unsealed · {r.purged_segments} purged · verified{' '}
                {r.verified_at}
              </p>
              {r.head ? (
                <p className="break-all font-mono text-[11px] text-zinc-400">
                  head #{r.head.seq} {r.head.segment_hash} — keep this as an anchor
                </p>
              ) : null}
              {r.errors.length ? (
                <ul className="list-disc pl-5 text-rose-300">
                  {r.errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              ) : null}
              {r.warnings.length ? (
                <ul className="list-disc pl-5 text-amber-300">
                  {r.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
        </QueryState>
      </Card>
      <Card title="Inclusion proof of one record">
        <form
          className="mb-2 flex gap-2 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            setProofId(obs.trim() || undefined);
          }}
        >
          <label htmlFor="obs-id" className="sr-only">
            record id
          </label>
          <input
            id="obs-id"
            placeholder="record (obs) id"
            className="w-80 rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 font-mono"
            value={obs}
            onChange={(e) => setObs(e.target.value)}
          />
          <button type="submit" className="rounded border border-zinc-700 px-2 py-0.5">
            get proof
          </button>
        </form>
        {proof.error ? <ErrorNote error={proof.error} /> : null}
        {proof.data ? (
          <>
            <p className="mb-1 text-[12px] text-zinc-400">
              Verify it anywhere with python verifier.py --proof proof.json
              (panoptis/evidence/verifier.py, standard library only).
            </p>
            <pre
              className="max-h-80 overflow-auto rounded border border-zinc-800 bg-zinc-900 p-2 font-mono text-[11px]"
              aria-label="inclusion proof"
            >
              {JSON.stringify(proof.data, null, 2)}
            </pre>
          </>
        ) : null}
      </Card>
    </SecurityShell>
  );
}
