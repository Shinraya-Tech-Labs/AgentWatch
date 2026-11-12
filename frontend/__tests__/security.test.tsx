import '@testing-library/jest-dom';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { axe, toHaveNoViolations } from 'jest-axe';
import type { ReactNode } from 'react';

import { MonitorStatus } from '../components/security/ui';
import AgentsPage from '../pages/security/agents';
import Approvals from '../pages/security/approvals';
import ControlsPage from '../pages/security/controls';
import Decisions from '../pages/security/decisions';
import EvidencePage from '../pages/security/evidence';
import SecurityOverview from '../pages/security/index';

expect.extend(toHaveNoViolations);

jest.mock('next/router', () => ({
  useRouter: () => ({ pathname: '/security', query: {}, push: jest.fn(), replace: jest.fn() }),
}));

const wrap = (ui: ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

const decision = (over: Record<string, unknown> = {}) => ({
  obs_id: '01OBS1',
  at: '2026-11-02T10:00:00+00:00',
  call_id: 'act-1',
  tool: 'bash',
  agent_id: 'coder',
  session_id: 's1',
  source: 'sdk',
  verdict: 'block',
  allowed: false,
  risk_score: 1,
  reason: '[threat:FS_DELETE_CRITICAL] Recursive deletion of /',
  mode: 'enforce',
  would_block: null,
  policy_version: null,
  threat_rules_version: 'sha256:abc',
  mutated: false,
  approval_state: null,
  findings: [
    {
      stage: 'threat',
      code: 'FS_DELETE_CRITICAL',
      severity: 'critical',
      message: 'Recursive deletion of /',
      recommends: 'block',
      evidence: {},
    },
  ],
  ...over,
});

type Route = (url: string, init?: RequestInit) => { status: number; body: unknown } | undefined;
let routes: Route[] = [];
let calls: { url: string; init?: RequestInit }[] = [];

function mockApi(...r: Route[]) {
  routes = r;
}

beforeEach(() => {
  calls = [];
  window.sessionStorage.setItem('agentwatch.apiKey', 'key-acme');
  global.fetch = jest.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    for (const r of routes) {
      const hit = r(String(url), init);
      if (hit)
        return {
          ok: hit.status < 400,
          status: hit.status,
          statusText: '',
          text: async () => JSON.stringify(hit.body),
        } as Response;
    }
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => JSON.stringify({}),
    } as Response;
  }) as unknown as typeof fetch;
});

const status =
  (over: Record<string, unknown> = {}) =>
  (url: string) =>
    url.endsWith('/status')
      ? {
          status: 200,
          body: {
            tenant_id: 'acme',
            recent_decisions: 3,
            verdicts: { allow: 1, block: 2 },
            last_decision_at: '2026-11-02T10:00:00+00:00',
            killed: false,
            kill_reason: null,
            suspended_agents: 0,
            suspended_sessions: 0,
            policy: { configured: false, digest: null },
            evidence: { head: null, segments: 0 },
            ...over,
          },
        }
      : undefined;

const decisions = (list: unknown[]) => (url: string) =>
  url.includes('/decisions') && !url.includes('/decisions/')
    ? {
        status: 200,
        body: { tenant_id: 'acme', decisions: list, count: list.length, next_after: null },
      }
    : undefined;

it('asks for a key and sends it with every request', async () => {
  window.sessionStorage.clear();
  mockApi(status(), decisions([]));
  wrap(<SecurityOverview />);
  expect(screen.getByLabelText('API key')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'key-acme' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(screen.getByText(/tenant acme/)).toBeInTheDocument());
  const sent = [...calls].reverse().find((c) => c.url.endsWith('/status'));
  expect((sent!.init!.headers as Record<string, string>)['x-api-key']).toBe('key-acme');
});

it('overview shows the backend counts and recent decisions, and the kill banner', async () => {
  mockApi(status({ killed: true, kill_reason: 'drill' }), decisions([decision()]));
  wrap(<SecurityOverview />);
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('EMERGENCY STOP ACTIVE'));
  expect(screen.getByRole('alert')).toHaveTextContent('drill');
  expect(
    screen.getByText('[threat:FS_DELETE_CRITICAL] Recursive deletion of /'),
  ).toBeInTheDocument();
  expect(
    screen.getByText('No RBAC policy is configured: tool authorization is not enforced.'),
  ).toBeInTheDocument();
});

it('shows empty, disconnected and rejected-key states instead of fake data', async () => {
  mockApi(status(), decisions([]));
  const { unmount } = wrap(<SecurityOverview />);
  await waitFor(() => expect(screen.getByText('No protected tool calls yet.')).toBeInTheDocument());
  unmount();
  global.fetch = jest.fn(async () => {
    throw new TypeError('network down');
  }) as unknown as typeof fetch;
  const r2 = wrap(<SecurityOverview />);
  await waitFor(() => expect(screen.getAllByText(/Disconnected/).length).toBeGreaterThan(0));
  expect(screen.getByText(/● disconnected/)).toBeInTheDocument();
  r2.unmount();
  mockApi((url) => ({ status: 401, body: { detail: 'Missing or invalid API key.' } }));
  global.fetch = jest.fn(
    async () =>
      ({
        ok: false,
        status: 401,
        statusText: '',
        text: async () => JSON.stringify({ detail: 'bad key' }),
      }) as Response,
  ) as unknown as typeof fetch;
  wrap(<SecurityOverview />);
  await waitFor(() => expect(screen.getAllByText(/not accepted/).length).toBeGreaterThan(0));
});

it('decisions view filters through the API and opens a call detail', async () => {
  mockApi(status(), decisions([decision()]), (url) =>
    url.includes('/decisions/act-1')
      ? {
          status: 200,
          body: {
            tenant_id: 'acme',
            call_id: 'act-1',
            decisions: [decision()],
            approval_requests: [],
            approval_decisions: [],
            executions: [],
            outputs: [],
          },
        }
      : undefined,
  );
  wrap(<Decisions />);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /details of bash call act-1/ })).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText('verdict'), { target: { value: 'block' } });
  await waitFor(() => expect(calls.some((c) => c.url.includes('verdict=block'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: /details of bash call act-1/ }));
  await waitFor(() => expect(screen.getByText(/executed: no/)).toBeInTheDocument());
});

it('approval sends the exact action digest shown to the user', async () => {
  const approval = {
    request_id: 'apr-1',
    tenant_id: 'acme',
    agent_id: 'coder',
    session_id: 's1',
    call_id: 'act-9',
    tool: 'bash',
    arguments: { command: 'git push --force origin main' },
    action_digest: 'sha256:deadbeef',
    reason: 'force push',
    risk_score: 0.8,
    created_at: 1_790_000_000,
    expires_at: 1_790_000_120,
    state: 'pending',
  };
  mockApi(status(), (url, init) =>
    url.includes('/approvals/apr-1') && init?.method === 'POST'
      ? { status: 200, body: { ...approval, state: 'approved', applied: true } }
      : url.includes('/approvals')
        ? { status: 200, body: { approvals: [approval], count: 1 } }
        : undefined,
  );
  wrap(<Approvals />);
  await waitFor(() =>
    expect(screen.getByLabelText('arguments to approve')).toHaveTextContent(
      'git push --force origin main',
    ),
  );
  fireEvent.change(screen.getByLabelText('your name'), { target: { value: 'alice' } });
  fireEvent.click(screen.getByRole('button', { name: /approve anyway/i }));
  await waitFor(() => expect(calls.some((c) => c.init?.method === 'POST')).toBe(true));
  const post = calls.find((c) => c.init?.method === 'POST')!;
  expect(JSON.parse(String(post.init!.body))).toEqual({
    approved: true,
    action_digest: 'sha256:deadbeef',
    decided_by: 'alice',
    comment: null,
  });
});

it('discovery never shows an unmonitored runtime as protected', async () => {
  const item = (status: string) => ({
    kind: 'mcp_server',
    runtime: 'cursor',
    name: `srv-${status}`,
    status,
    source: '/x',
    confidence: 0.9,
    reason: 'r',
    discovered_at: 1,
    details: {},
  });
  mockApi(status(), (url) =>
    url.endsWith('/discovery')
      ? {
          status: 200,
          body: {
            tenant_id: 'acme',
            inventory: { items: [item('MONITORED'), item('UNMONITORED'), item('UNKNOWN')] },
            recorded_at: null,
          },
        }
      : url.endsWith('/agents')
        ? { status: 200, body: { tenant_id: 'acme', agents: [], killed: false } }
        : undefined,
  );
  wrap(<AgentsPage />);
  await waitFor(() => expect(screen.getByText('srv-UNMONITORED')).toBeInTheDocument());
  const badge = (s: string) => screen.getAllByText(s).find((e) => e.tagName === 'SPAN')!;
  expect(badge('MONITORED').className).toContain('emerald');
  expect(badge('UNMONITORED').className).not.toContain('emerald');
  expect(badge('UNKNOWN').className).not.toContain('emerald');
  const { container } = render(<MonitorStatus s="UNSUPPORTED" />);
  expect(container.firstChild).not.toHaveClass('text-emerald-300');
});

it('emergency stop needs a reason and a confirmation, and a refused key is explained', async () => {
  mockApi(status(), (url, init) =>
    url.endsWith('/control/kill') && init?.method === 'POST'
      ? { status: 403, body: { detail: 'Operator credential required for this route.' } }
      : url.endsWith('/control')
        ? {
            status: 200,
            body: {
              tenant_id: 'acme',
              killed: false,
              kill_reason: null,
              kill_actor: null,
              killed_at: null,
              local_killed: false,
              suspended_agents: {},
              suspended_sessions: {},
            },
          }
        : undefined,
  );
  wrap(<ControlsPage />);
  const stop = await screen.findByRole('button', { name: 'Emergency stop…' });
  expect(stop).toBeDisabled();
  fireEvent.change(screen.getByLabelText('control reason'), { target: { value: 'drill' } });
  fireEvent.click(stop);
  fireEvent.click(screen.getByRole('button', { name: 'Confirm: stop all agents' }));
  await waitFor(() => expect(screen.getByText(/operator only/)).toBeInTheDocument());
});

it('evidence view reports a failed verification with its errors', async () => {
  mockApi(status(), (url) =>
    url.includes('/evidence/verify')
      ? {
          status: 200,
          body: {
            ok: false,
            tenant_id: 'acme',
            segments_checked: 2,
            observations_checked: 9,
            unsealed_observations: 0,
            purged_segments: 0,
            head: { seq: 2, segment_hash: 'ab'.repeat(32) },
            errors: ['segment default:00000001: merkle root mismatch'],
            warnings: [],
            verified_at: 'now',
          },
        }
      : undefined,
  );
  wrap(<EvidencePage />);
  await waitFor(() => expect(screen.getByText(/FAILED/)).toBeInTheDocument());
  expect(screen.getByText('segment default:00000001: merkle root mismatch')).toBeInTheDocument();
});

it.each([
  ['overview', <SecurityOverview key="o" />],
  ['decisions', <Decisions key="d" />],
  ['approvals', <Approvals key="a" />],
  ['controls', <ControlsPage key="c" />],
  ['evidence', <EvidencePage key="e" />],
])('%s has no detectable accessibility violations', async (_name, page) => {
  mockApi(status(), decisions([decision()]));
  const { container } = wrap(page);
  await waitFor(() => expect(screen.getByText(/tenant acme/)).toBeInTheDocument());
  expect(await axe(container)).toHaveNoViolations();
});

it('policy view shows the active policy and explains a dry-run denial', async () => {
  const { default: PolicyPage } = await import('../pages/security/policy');
  mockApi(status(), (url, init) =>
    url.endsWith('/rbac/policy')
      ? {
          status: 200,
          body: {
            configured: true,
            tenant_id: 'acme',
            digest: 'abc123',
            version: '1.0',
            default: 'deny',
            loaded_at: 1,
            last_error: null,
            roles: {
              coder: {
                allowed_tools: ['bash'],
                forbidden_tools: [],
                require_approval: [],
                rate_limits: {},
              },
            },
            agents: { coder: ['coder'] },
          },
        }
      : url.endsWith('/rbac/check') && init?.method === 'POST'
        ? {
            status: 200,
            body: {
              tenant_id: 'acme',
              agent_id: 'coder',
              tool: 'bash',
              verdict: 'block',
              authorized: false,
              reasons: ["Role 'coder' blocks commands matching 'sudo *'"],
            },
          }
        : undefined,
  );
  wrap(<PolicyPage />);
  await waitFor(() => expect(screen.getByText(/digest abc123/)).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText('check agent'), { target: { value: 'coder' } });
  fireEvent.change(screen.getByLabelText('check tool'), { target: { value: 'bash' } });
  fireEvent.change(screen.getByLabelText('check arguments'), {
    target: { value: '{"command": "sudo ls"}' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'check' }));
  await waitFor(() => expect(screen.getByText(/blocks commands matching/)).toBeInTheDocument());
  const post = calls.find((c) => c.init?.method === 'POST')!;
  expect(JSON.parse(String(post.init!.body))).toEqual({
    agent_id: 'coder',
    tool: 'bash',
    arguments: { command: 'sudo ls' },
  });
  fireEvent.change(screen.getByLabelText('check arguments'), { target: { value: 'not json' } });
  fireEvent.click(screen.getByRole('button', { name: 'check' }));
  expect(screen.getByText('Arguments must be a JSON object.')).toBeInTheDocument();
});
