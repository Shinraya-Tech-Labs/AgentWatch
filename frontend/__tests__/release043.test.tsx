/**
 * 0.4.3: a stale local key is refreshed once, approval cards lead with what the call does,
 * timestamps are in the viewer's zone, and the decisions page has no unexplained panels.
 */
import '@testing-library/jest-dom';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';

import { DecisionButtons, localTime, toDate } from '../components/security/ui';
import { resetLocalSignIn } from '../lib/local';
import { ApiError, protect, setKey, STALE_LOCAL_KEY } from '../lib/protect/client';
import { describeRisk, shownAction } from '../lib/protect/risk';
import Decisions from '../pages/security/decisions';

jest.mock('next/router', () => ({
  useRouter: () => ({ pathname: '/security', query: {}, push: jest.fn(), replace: jest.fn() }),
}));

type Reply = { status: number; body: unknown };
const respond = ({ status, body }: Reply) =>
  Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: '',
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as Response);

const session = (key: string) => ({
  mode: 'local',
  workspace: 'local',
  profiles: [
    {
      id: 'local',
      label: 'This machine',
      tenant_id: 'default',
      key,
      sample_data: false,
      operator: true,
    },
  ],
});

let calls: { url: string; key?: string }[] = [];

function serve(route: (url: string, key?: string) => Reply) {
  calls = [];
  global.fetch = jest.fn((url: RequestInfo | URL, init?: RequestInit) => {
    const key = (init?.headers as Record<string, string> | undefined)?.['x-api-key'];
    calls.push({ url: String(url), key });
    return respond(route(String(url), key));
  }) as jest.Mock;
}

beforeEach(() => {
  window.sessionStorage.clear();
  resetLocalSignIn();
});

describe('a stale key in local mode', () => {
  it('re-fetches /api/local/session and retries once with the new key', async () => {
    setKey('old-key');
    serve((url, key) =>
      url === '/api/local/session'
        ? { status: 200, body: session('new-key') }
        : key === 'new-key'
          ? { status: 200, body: { ok: true } }
          : { status: 401, body: { detail: 'invalid API key' } },
    );
    await expect(protect('/status')).resolves.toEqual({ ok: true });
    expect(calls.map((c) => [c.url, c.key])).toEqual([
      ['/api/v1/protect/status', 'old-key'],
      ['/api/local/session', undefined],
      ['/api/v1/protect/status', 'new-key'],
    ]);
    expect(window.sessionStorage.getItem('agentwatch.apiKey')).toBe('new-key');
  });

  it('says to restart panoptis up when the refreshed key is rejected too', async () => {
    setKey('old-key');
    serve((url) =>
      url === '/api/local/session'
        ? { status: 200, body: session('still-wrong') }
        : { status: 401, body: { detail: 'invalid API key' } },
    );
    const err = (await protect('/status').catch((e: unknown) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toBe(STALE_LOCAL_KEY);
    expect(err.message).toMatch(/restart panoptis up/i);
    expect(calls).toHaveLength(3); // one retry, not a loop
  });

  it('leaves a 401 alone on a server without local mode', async () => {
    setKey('k');
    serve((url) =>
      url === '/api/local/session'
        ? { status: 404, body: { detail: 'Not Found' } }
        : { status: 401, body: { detail: 'invalid API key' } },
    );
    const err = (await protect('/status').catch((e: unknown) => e)) as ApiError;
    expect(err.message).toBe('invalid API key');
    await protect('/status').catch(() => null);
    // the session route is asked once per page, not on every poll
    expect(calls.filter((c) => c.url === '/api/local/session')).toHaveLength(1);
  });
});

describe('approval cards', () => {
  const del = {
    risk_score: 0.8,
    reason: '[threat:FS_DELETE_RECURSIVE] Recursive forced deletion: ./project',
    arguments: { command: 'rm -rf ./project', description: 'clean' },
    findings: [
      {
        code: 'FS_DELETE_RECURSIVE',
        message: 'Recursive forced deletion: ./project',
        evidence: { targets: ['./project'] },
      },
    ],
  };

  it('say in plain English what the call does', () => {
    expect(describeRisk(del)).toEqual({
      sentence: 'This deletes ./project and everything inside it.',
      high: true,
    });
    expect(shownAction(del)).toBe('rm -rf ./project');
    expect(
      describeRisk({
        risk_score: 0.3,
        reason: 'x',
        findings: [{ code: 'SOMETHING_NEW', message: 'Calls an unknown host' }],
      }),
    ).toEqual({ sentence: 'PANOPTIS flagged this: Calls an unknown host.', high: false });
    expect(shownAction({ arguments: { file_path: '/repo/.env', content: 'A=1' } })).toBe(
      '/repo/.env',
    );
  });

  it('make Approve look different for a high-risk call', () => {
    const decide = jest.fn();
    const { rerender } = render(<DecisionButtons high busy={false} onDecide={decide} />);
    const approve = screen.getByRole('button', { name: /approve anyway/i });
    expect(approve).toHaveAttribute('data-risk', 'high');
    fireEvent.click(approve);
    expect(decide).toHaveBeenCalledWith(true);
    rerender(<DecisionButtons high={false} busy={false} onDecide={decide} />);
    expect(screen.getByRole('button', { name: 'Approve' })).toHaveAttribute('data-risk', 'normal');
  });
});

describe('timestamps', () => {
  it('are shown in the viewer’s local time zone', () => {
    const at = '2026-11-12T08:30:05+00:00';
    const d = new Date(at);
    const pad = (n: number) => String(n).padStart(2, '0');
    const expected = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
      d.getHours(),
    )}:${pad(d.getMinutes())}:05`;
    expect(localTime(at)).toBe(expected);
    expect(localTime(Date.parse(at) / 1000)).toBe(expected);
    // an ISO string without a zone is UTC, as the API writes it
    expect(toDate('2026-11-12T08:30:05')?.toISOString()).toBe('2026-11-12T08:30:05.000Z');
    expect(localTime(null)).toBe('');
  });
});

describe('decisions page', () => {
  const wrap = (ui: ReactNode) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
  };

  it('does not show empty "Loop detection" / "Intent grounding" panels', async () => {
    setKey('k');
    serve((url) =>
      url.includes('/stages/')
        ? { status: 200, body: { events: [], count: 0 } }
        : url.includes('/decisions')
          ? { status: 200, body: { tenant_id: 'default', decisions: [], count: 0 } }
          : { status: 200, body: {} },
    );
    wrap(<Decisions />);
    expect(await screen.findByText('No decisions match.')).toBeInTheDocument();
    expect(screen.queryByText('Loop detection')).toBeNull();
    expect(screen.queryByText('Intent grounding')).toBeNull();
    expect(screen.queryByText('Repeated calls')).toBeNull();
  });
});
