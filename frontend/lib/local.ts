/**
 * Local mode (`panoptis up`): the server hands the dashboard its keys at /api/local/session,
 * only for a loopback request on the same origin. Elsewhere (Docker Compose, a shared host)
 * that route does not exist and the dashboard asks for a key as before.
 */
import { useEffect, useState } from 'react';

import { getKey, setKey } from './protect/client';

export interface LocalProfile {
  id: string;
  label: string;
  tenant_id: string;
  key: string;
  sample_data: boolean;
  operator: boolean;
}

const PROFILES = 'panoptis.localProfiles';
const ACTIVE = 'panoptis.localProfile';
const EVENT = 'panoptis-local';

function read<T>(name: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(name);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(name: string, value: unknown): void {
  try {
    window.sessionStorage.setItem(name, JSON.stringify(value));
  } catch {
    /* storage unavailable: local sign-in lasts for this page only */
  }
}

export function localProfiles(): LocalProfile[] {
  if (typeof window === 'undefined') return [];
  return read<LocalProfile[]>(PROFILES) ?? [];
}

export function activeProfile(): LocalProfile | null {
  const id = typeof window === 'undefined' ? null : read<string>(ACTIVE);
  const key = getKey();
  return localProfiles().find((p) => p.id === id && p.key === key) ?? null;
}

export function useProfiles(): { profiles: LocalProfile[]; active: LocalProfile | null } {
  const [state, setState] = useState<{ profiles: LocalProfile[]; active: LocalProfile | null }>({
    profiles: [],
    active: null,
  });
  useEffect(() => {
    const sync = () => setState({ profiles: localProfiles(), active: activeProfile() });
    sync();
    window.addEventListener(EVENT, sync);
    window.addEventListener('agentwatch-key', sync);
    return () => {
      window.removeEventListener(EVENT, sync);
      window.removeEventListener('agentwatch-key', sync);
    };
  }, []);
  return state;
}

export function switchProfile(id: string): void {
  const p = localProfiles().find((x) => x.id === id);
  if (!p) return;
  write(ACTIVE, p.id);
  setKey(p.key);
  window.dispatchEvent(new Event(EVENT));
}

let attempted = false;

/** Ask the server for local keys once per page load; true when local mode signed us in. */
export async function tryLocalSignIn(): Promise<boolean> {
  if (attempted || typeof window === 'undefined') return false;
  attempted = true;
  try {
    const res = await fetch('/api/local/session', { headers: { accept: 'application/json' } });
    if (!res.ok) return false;
    const body = (await res.json()) as { mode?: string; profiles?: LocalProfile[] };
    const profiles = Array.isArray(body?.profiles) ? body.profiles : [];
    if (body?.mode !== 'local' || !profiles.length) return false;
    write(PROFILES, profiles);
    const current = getKey();
    const keep = profiles.find((p) => p.key === current);
    write(ACTIVE, (keep ?? profiles[0]).id);
    if (!keep) setKey(profiles[0].key);
    window.dispatchEvent(new Event(EVENT));
    return true;
  } catch {
    return false;
  }
}

/** Test hook: let the next page load try again. */
export function resetLocalSignIn(): void {
  attempted = false;
}
