/**
 * Plain-English risk for a call waiting for approval: what it will do, in one sentence,
 * and whether it is high risk (destructive or hard to undo), so the approval card can
 * lead with that and make "Approve" look different for those calls.
 */
import type { Approval, ApprovalFinding } from './types';

export interface Risk {
  sentence: string;
  high: boolean;
}

const list = (xs: string[]) =>
  xs.length <= 1 ? (xs[0] ?? '') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`;

function targets(f: ApprovalFinding): string[] {
  const t = f.evidence?.targets;
  return Array.isArray(t) ? t.map(String).filter(Boolean) : [];
}

const SENTENCES: Record<string, (f: ApprovalFinding) => string> = {
  FS_DELETE_CRITICAL: (f) =>
    targets(f).length
      ? `This deletes ${list(targets(f))}: a system or home location.`
      : 'This deletes a system or home location.',
  FS_DELETE_RECURSIVE: (f) =>
    targets(f).length
      ? `This deletes ${list(targets(f))} and everything inside it.`
      : 'This deletes a folder and everything inside it.',
  PATTERN_FS_DELETE_HIGH: () => 'This deletes files or folders.',
  GIT_FORCE_PUSH: () => 'This force-pushes and can overwrite history on the remote.',
  PERMISSIONS_WORLD_WRITABLE: () => 'This makes files writable by every user on the machine.',
  PATTERN_DB_DESTRUCTIVE: () => 'This can delete database tables or data.',
  PRIVILEGE_ESCALATION: () => 'This runs with administrator (sudo) rights.',
};

// destructive or hard to undo: the approve button says so
const HIGH = new Set([
  'FS_DELETE_CRITICAL',
  'FS_DELETE_RECURSIVE',
  'PATTERN_FS_DELETE_HIGH',
  'GIT_FORCE_PUSH',
  'PERMISSIONS_WORLD_WRITABLE',
  'PATTERN_DB_DESTRUCTIVE',
  'PRIVILEGE_ESCALATION',
]);

export function describeRisk(a: Pick<Approval, 'findings' | 'risk_score' | 'reason'>): Risk {
  const findings = Array.isArray(a.findings) ? a.findings : [];
  const known = findings.find((f) => SENTENCES[f.code]);
  const high = findings.some((f) => HIGH.has(f.code)) || Number(a.risk_score ?? 0) >= 0.7;
  if (known) return { sentence: SENTENCES[known.code](known), high };
  const first = findings.find((f) => f.message)?.message ?? a.reason ?? '';
  return {
    sentence: first ? `PANOPTIS flagged this: ${first}.` : 'PANOPTIS flagged this call.',
    high,
  };
}

/** The thing to approve, as the person should read it: the command, else the target. */
export function shownAction(a: Pick<Approval, 'arguments'>): string {
  const args = a.arguments ?? {};
  for (const k of ['command', 'cmd', 'script', 'file_path', 'path', 'url', 'query']) {
    if (typeof args[k] === 'string' && args[k]) return args[k] as string;
  }
  return JSON.stringify(args);
}
