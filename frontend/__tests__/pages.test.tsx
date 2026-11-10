import '@testing-library/jest-dom';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { GraphView } from '../components/v3/GraphView';
import Live from '../pages/live';
import Query from '../pages/query';

jest.mock('next/router', () => ({
  useRouter: () => ({ pathname: '/', query: {}, push: jest.fn(), replace: jest.fn() }),
}));

const wrap = (ui: ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

const event = {
  event_id: 'e1-aaaa-bbbb',
  kind: 'TOOL_INVOCATION',
  operation: 'search',
  status: 'ERROR',
  actor: 'agent:a',
  object: 'tool:search',
  run_id: 'run-1234567',
  time: { start: '2026-01-01T00:00:00.123+00:00', end: null, duration_ms: 12.5 },
};

beforeEach(() => {
  global.fetch = jest.fn(async (url: string) => {
    const body = String(url).includes('/live')
      ? {
          recent_events: [event],
          recent_runs: [],
          active_runs: [],
          actors: { 'agent:a': 1 },
          unresolved: [],
          diagnostic_count: 0,
        }
      : String(url).includes('/status')
        ? {
            observations: 5,
            capabilities: [{ name: 'lab.replay', maturity: 'EXPERIMENTAL' }],
            reexecution_enabled: false,
          }
        : { interp_id: 'i', runs: [] };
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => JSON.stringify(body),
    } as Response;
  }) as unknown as typeof fetch;
});

it('LIVE renders recent events, statuses and capability maturity from the v3 API', async () => {
  wrap(<Live />);
  await waitFor(() => expect(screen.getByText('search')).toBeInTheDocument());
  expect(screen.getByText('ERROR')).toBeInTheDocument();
  expect(screen.getByText('lab.replay')).toBeInTheDocument();
  expect(screen.getAllByText('EXPERIMENTAL').length).toBeGreaterThan(0);
});

it('QUERY explains that answers cite evidence', () => {
  wrap(<Query />);
  expect(screen.getByText(/No language model authors facts/)).toBeInTheDocument();
});

it('GraphView renders nodes and dims nodes outside the highlight', () => {
  const nodes = [
    { node: 'event:a', type: 'event' as const, label: 'OPERATION plan', status: 'OK' },
    { node: 'event:b', type: 'event' as const, label: 'TOOL_INVOCATION search', status: 'ERROR' },
  ];
  const relations = [
    {
      rel_id: 'r',
      view: 'EXECUTION' as const,
      type: 'CONTAINS',
      tail: ['event:a'],
      head: ['event:b'],
      basis: 'DECLARED',
      evidence_class: null,
      confidence: 1,
      run_id: 'x',
      evidence: [],
      derived_by: 't',
      attributes: {},
    },
  ];
  const { container } = render(
    <GraphView nodes={nodes} relations={relations} highlight={new Set(['event:a'])} />,
  );
  const groups = container.querySelectorAll('g[opacity]');
  const opacities = Array.from(groups).map((g) => g.getAttribute('opacity'));
  expect(opacities).toContain('1');
  expect(opacities).toContain('0.12');
});
