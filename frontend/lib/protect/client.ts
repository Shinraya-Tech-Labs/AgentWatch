/**
 * Client for the AgentWatch protection API (/api/v1/protect, docs/PROTECT_API.md).
 *
 * The dashboard holds no credential of its own: the user signs in with their API key
 * (a tenant key, or the operator key), kept in sessionStorage for the tab only, and
 * every request carries it. The server decides what the key may see and do, so the
 * dashboard shows exactly the caller's tenant, and operator-only controls are refused
 * by the server, not hidden by the browser.
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type {
  Agents,
  Approval,
  CallDetail,
  ControlState,
  DecisionPage,
  Discovery,
  EvidenceReport,
  Mcp,
  Permissions,
  PolicyView,
  ProtectStatus,
  RbacCheck,
  StageEvents,
} from './types';

export const PROTECT_BASE = process.env.NEXT_PUBLIC_PROTECT_API_URL ?? '/api/v1/protect';
const KEY_STORAGE = 'agentwatch.apiKey';
export const POLL_MS = 5000;

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function getKey(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.sessionStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

export function setKey(key: string | null): void {
  try {
    if (key) window.sessionStorage.setItem(KEY_STORAGE, key);
    else window.sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage unavailable: the key lives only for this page */
  }
  window.dispatchEvent(new Event('agentwatch-key'));
}

export function useApiKey(): [string | null, (k: string | null) => void] {
  const [key, setState] = useState<string | null>(null);
  useEffect(() => {
    const read = () => setState(getKey());
    read();
    window.addEventListener('agentwatch-key', read);
    return () => window.removeEventListener('agentwatch-key', read);
  }, []);
  return [key, setKey];
}

export async function protect<T>(path: string, init?: RequestInit): Promise<T> {
  const key = getKey();
  let res: Response;
  try {
    res = await fetch(`${PROTECT_BASE}${path}`, {
      ...init,
      headers: {
        'content-type': 'application/json',
        ...(key ? { 'x-api-key': key } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError(0, 'The PANOPTIS API is unreachable');
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : res.statusText || `HTTP ${res.status}`;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

const enc = encodeURIComponent;
const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return p.length ? `?${p.map(([k, v]) => `${k}=${enc(String(v))}`).join('&')}` : '';
};

// Queries run only with a key. Every hook subscribes to the key, so signing in or out
// re-renders the page that owns the hook (the hooks live above the shell).
function useSignedIn(): boolean {
  const [key] = useApiKey();
  return !!key;
}
const live = (enabled: boolean) => ({
  refetchInterval: POLL_MS,
  staleTime: 0,
  retry: false as const,
  enabled,
});

// Unknown or partial bodies become well-formed empty shapes, never a crash.
const arr = <T>(v: any): T[] => (Array.isArray(v) ? v : []);
const obj = <T>(v: any): T => (v && typeof v === 'object' && !Array.isArray(v) ? v : ({} as T));

const normStatus = (d: any): ProtectStatus => ({
  ...obj<ProtectStatus>(d),
  verdicts: obj(d?.verdicts),
  policy: { configured: !!d?.policy?.configured, digest: d?.policy?.digest ?? null },
  evidence: { head: d?.evidence?.head ?? null, segments: Number(d?.evidence?.segments ?? 0) },
});
const normDecision = (d: any) => ({
  ...obj<any>(d),
  findings: arr(d?.findings),
  risk_score: Number(d?.risk_score ?? 0),
});
const normPage = (d: any): DecisionPage => ({
  tenant_id: d?.tenant_id ?? '',
  decisions: arr(d?.decisions).map(normDecision),
  count: Number(d?.count ?? 0),
  next_after: d?.next_after ?? null,
});
const normEvidence = (d: any): EvidenceReport => ({
  ...obj<EvidenceReport>(d),
  errors: arr(d?.errors),
  warnings: arr(d?.warnings),
});
const normControl = (d: any): ControlState => ({
  ...obj<ControlState>(d),
  suspended_agents: obj(d?.suspended_agents),
  suspended_sessions: obj(d?.suspended_sessions),
});

export const useStatus = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'status'],
    queryFn: async () => normStatus(await protect('/status')),
    ...live(signed),
  });
};

export interface DecisionFilters {
  verdict?: string;
  tool?: string;
  agent_id?: string;
  stage?: string;
  limit?: number;
}
export const useDecisions = (f: DecisionFilters = {}) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'decisions', f],
    queryFn: async () => normPage(await protect(`/decisions${qs({ limit: 100, ...f })}`)),
    ...live(signed),
    placeholderData: keepPreviousData, // keep the table while a new filter loads
  });
};

export const useCallDetail = (callId?: string) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'call', callId],
    queryFn: async () => {
      const d = await protect<CallDetail>(`/decisions/${enc(callId!)}`);
      return {
        ...d,
        decisions: arr(d?.decisions).map(normDecision),
        approval_requests: arr(d?.approval_requests),
        approval_decisions: arr(d?.approval_decisions),
        executions: arr(d?.executions),
        outputs: arr(d?.outputs),
      } as CallDetail;
    },
    enabled: !!callId && signed,
    retry: false,
  });
};

export const useStageEvents = (stage: 'loops' | 'intent') => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', stage],
    queryFn: async () => {
      const d = await protect<StageEvents>(`/${stage}`);
      return {
        ...d,
        events: arr<StageEvents['events'][number]>(d?.events).map((e) => ({
          ...e,
          findings: arr(e?.findings),
        })),
      } as StageEvents;
    },
    ...live(signed),
  });
};

export const useAgents = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'agents'],
    queryFn: async () => {
      const d = await protect<Agents>('/agents');
      return {
        ...d,
        agents: arr<Agents['agents'][number]>(d?.agents).map((a) => ({
          ...a,
          sessions: arr(a?.sessions),
        })),
      } as Agents;
    },
    ...live(signed),
  });
};

export const useMcp = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'mcp'],
    queryFn: async () => {
      const d = await protect<Mcp>('/mcp');
      return {
        ...d,
        servers: arr<Mcp['servers'][number]>(d?.servers).map((x) => ({
          ...x,
          verdicts: obj(x?.verdicts),
          tools: obj(x?.tools),
        })),
        recent: arr(d?.recent),
      } as Mcp;
    },
    ...live(signed),
  });
};

export const useDiscovery = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'discovery'],
    queryFn: async () => {
      const d = await protect<Discovery>('/discovery');
      const inv = d?.inventory;
      return { ...d, inventory: inv ? { ...inv, items: arr(inv.items) } : null } as Discovery;
    },
    ...live(signed),
  });
};

export const useApprovals = (state?: string) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'approvals', state],
    queryFn: async () => {
      const d = await protect<{ approvals: Approval[]; count: number }>(
        `/approvals${qs({ state })}`,
      );
      const approvals = arr<Approval>(d?.approvals).map((a) => ({
        ...a,
        arguments: obj<Record<string, unknown>>(a?.arguments),
      }));
      return { approvals, count: approvals.length };
    },
    ...live(signed),
    refetchInterval: 2000,
  });
};

export function useDecideApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: {
      request_id: string;
      approved: boolean;
      action_digest: string;
      decided_by: string;
      comment?: string;
    }) =>
      protect<Approval & { applied: boolean }>(`/approvals/${enc(v.request_id)}`, {
        method: 'POST',
        body: JSON.stringify({
          approved: v.approved,
          action_digest: v.action_digest,
          decided_by: v.decided_by,
          comment: v.comment || null,
        }),
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['protect'] }),
  });
}

export const usePolicy = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'policy'],
    queryFn: async () => {
      const d = await protect<PolicyView>('/rbac/policy');
      const roles = Object.fromEntries(
        Object.entries(obj<PolicyView['roles']>(d?.roles)).map(([k, r]) => [
          k,
          {
            ...r,
            allowed_tools: arr<string>(r?.allowed_tools),
            forbidden_tools: arr<string>(r?.forbidden_tools),
            require_approval: arr<string>(r?.require_approval),
          },
        ]),
      );
      return { ...d, roles, agents: obj<PolicyView['agents']>(d?.agents) } as PolicyView;
    },
    retry: false,
    enabled: signed,
  });
};

export const usePermissions = (agent?: string) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'permissions', agent],
    queryFn: () => protect<Permissions>(`/rbac/agents/${enc(agent!)}/permissions`),
    enabled: !!agent && signed,
    retry: false,
  });
};

export const useRbacCheck = () =>
  useMutation({
    mutationFn: (v: { agent_id: string; tool: string; arguments: Record<string, unknown> }) =>
      protect<RbacCheck>('/rbac/check', { method: 'POST', body: JSON.stringify(v) }).then((d) => ({
        ...d,
        reasons: arr<string>(d?.reasons),
      })),
  });

export const useControl = () => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'control'],
    queryFn: async () => normControl(await protect('/control')),
    ...live(signed),
  });
};

export function useControlAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { path: string; reason: string; actor?: string }) =>
      protect<Record<string, unknown>>(`/control${v.path}`, {
        method: 'POST',
        body: JSON.stringify({ reason: v.reason, actor: v.actor || 'dashboard' }),
      }),
    onSettled: () => qc.invalidateQueries({ queryKey: ['protect'] }),
  });
}

export const useEvidence = (seal: boolean, nonce: number) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'evidence', seal, nonce],
    queryFn: async () => normEvidence(await protect(`/evidence/verify${qs({ seal })}`)),
    retry: false,
    staleTime: Infinity,
    enabled: signed,
  });
};

export const useProof = (obsId?: string) => {
  const signed = useSignedIn();
  return useQuery({
    queryKey: ['protect', 'proof', obsId],
    queryFn: () => protect<Record<string, unknown>>(`/evidence/proof/${enc(obsId!)}`),
    enabled: !!obsId && signed,
    retry: false,
  });
};
