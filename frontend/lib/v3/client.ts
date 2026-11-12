import { useMutation, useQuery } from '@tanstack/react-query';

import { fetchWithKey } from '../protect/client';
import type {
  CompareResult,
  EventDetail,
  GraphResponse,
  ProvenanceEvidence,
  Run,
  RunDetail,
} from './types';

export const V3_BASE = process.env.NEXT_PUBLIC_API_V3_URL ?? '/api/v3';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function v3<T>(path: string, init?: RequestInit): Promise<T> {
  // the signed-in key (local mode, or a direct API connection); behind the Compose proxy the
  // server attaches its own and the browser holds none
  // (a stale local key is refreshed once, as on every other page)
  let res: Response;
  try {
    res = await fetchWithKey(`${V3_BASE}${path}`, init);
  } catch (e) {
    const err = e as { status?: number; message?: string };
    throw new ApiError(err.status ?? 0, err.message ?? 'The PANOPTIS API is unreachable');
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body ? String(body.detail) : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
}

const enc = encodeURIComponent;

export const useRuns = (refetchMs?: number) =>
  useQuery({
    queryKey: ['v3', 'runs'],
    queryFn: () => v3<{ interp_id: string; runs: Run[] }>('/runs'),
    refetchInterval: refetchMs,
  });

export const useRun = (ref?: string) =>
  useQuery({
    queryKey: ['v3', 'run', ref],
    queryFn: () => v3<RunDetail>(`/runs/${enc(ref!)}`),
    enabled: !!ref,
  });

export const useGraph = (ref: string | undefined, view: string) =>
  useQuery({
    queryKey: ['v3', 'graph', ref, view],
    queryFn: () => v3<GraphResponse>(`/runs/${enc(ref!)}/graph?view=${view}`),
    enabled: !!ref,
  });

export const useEvent = (id?: string | null) =>
  useQuery({
    queryKey: ['v3', 'event', id],
    queryFn: () => v3<EventDetail>(`/events/${enc(id!)}`),
    enabled: !!id,
  });

export const useLive = () =>
  useQuery({
    queryKey: ['v3', 'live'],
    queryFn: () => v3<Record<string, any>>('/live'),
    refetchInterval: 3000,
  });

export const useStatus = () =>
  useQuery({ queryKey: ['v3', 'status'], queryFn: () => v3<Record<string, any>>('/status') });

export const useCompare = (a?: string, b?: string) =>
  useQuery({
    queryKey: ['v3', 'compare', a, b],
    queryFn: () => v3<CompareResult>(`/compare?a=${enc(a!)}&b=${enc(b!)}`),
    enabled: !!a && !!b && a !== b,
  });

export const useProvenance = (node?: string | null, run?: string) =>
  useQuery({
    queryKey: ['v3', 'provenance', node, run],
    queryFn: () => v3<Record<string, any>>(`/provenance/${node}${run ? `?run=${enc(run)}` : ''}`),
    enabled: !!node,
  });

export const useProvenanceEvidence = (run?: string) =>
  useQuery({
    queryKey: ['v3', 'provenance-evidence', run],
    queryFn: () => v3<ProvenanceEvidence>(`/runs/${enc(run!)}/provenance-evidence`),
    enabled: !!run,
  });

export const useMotifStats = () =>
  useQuery({ queryKey: ['v3', 'motifs'], queryFn: () => v3<Record<string, any>[]>('/motifs') });

export const useGenome = (scope: string) =>
  useQuery({
    queryKey: ['v3', 'genome', scope],
    queryFn: () => v3<Record<string, any>>(`/genome?scope=${enc(scope)}`),
    enabled: !!scope,
  });

export const useDrift = (baseline: string, candidate: string) =>
  useQuery({
    queryKey: ['v3', 'drift', baseline, candidate],
    queryFn: () =>
      v3<Record<string, any>>(`/drift?baseline=${enc(baseline)}&candidate=${enc(candidate)}`),
    enabled: !!baseline && !!candidate,
  });

export const useCauses = (event?: string | null) =>
  useQuery({
    queryKey: ['v3', 'causes', event],
    queryFn: () => v3<Record<string, any>>(`/causes/${enc(event!)}`),
    enabled: !!event,
  });

export const useBranches = (run?: string) =>
  useQuery({
    queryKey: ['v3', 'branches', run],
    queryFn: () => v3<Record<string, any>>(`/runs/${enc(run!)}/branches`),
    enabled: !!run,
  });

export const post = <T>(path: string, body: unknown) =>
  v3<T>(path, { method: 'POST', body: JSON.stringify(body) });

export const useQueryAsk = () =>
  useMutation({ mutationFn: (text: string) => post<Record<string, any>>('/query', { text }) });
