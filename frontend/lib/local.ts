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
// null until /api/local/session answered once; false on a server without local mode
let isLocal: boolean | null = null;
let inflight: Promise<boolean> | null = null;

/** Fetch the local session and adopt its key. True when the server is in local mode. */
async function loadLocalSession(): Promise<boolean> {
  try {
    const res = await fetch('/api/local/session', { headers: { accept: 'application/json' } });
    if (!res.ok) {
      isLocal = false;
      return false;
    }
    const body = (await res.json()) as { mode?: string; profiles?: LocalProfile[] };
    const profiles = Array.isArray(body?.profiles) ? body.profiles : [];
    if (body?.mode !== 'local' || !profiles.length) {
      isLocal = false;
      return false;
    }
    isLocal = true;
    write(PROFILES, profiles);
    const current = getKey();
    const activeId = read<string>(ACTIVE);
    // keep the profile the user picked; a key that changed under it is replaced
    const keep = profiles.find((p) => p.key === current) ?? profiles.find((p) => p.id === activeId);
    const pick = keep ?? profiles[0];
    write(ACTIVE, pick.id);
    if (pick.key !== current) setKey(pick.key);
    window.dispatchEvent(new Event(EVENT));
    return true;
  } catch {
    return false;
  }
}

/** Ask the server for local keys once per page load; true when local mode signed us in. */
export async function tryLocalSignIn(): Promise<boolean> {
  if (attempted || typeof window === 'undefined') return false;
  attempted = true;
  return loadLocalSession();
}

/**
 * After a 401: re-fetch /api/local/session (the key in this tab may belong to an earlier
 * `panoptis up` or another workspace). True when local mode answered, so the caller retries
 * once with the key now stored. Concurrent callers share one request.
 */
export async function refreshLocalSession(): Promise<boolean> {
  if (typeof window === 'undefined' || isLocal === false) return false;
  inflight ??= loadLocalSession().finally(() => {
    inflight = null;
  });
  return inflight;
}

/** Test hook: let the next page load try again. */
export function resetLocalSignIn(): void {
  attempted = false;
  isLocal = null;
  inflight = null;
}
