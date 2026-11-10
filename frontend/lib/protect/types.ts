/** Response shapes of /api/v1/protect (see docs/PROTECT_API.md). */

export interface Finding {
  stage: string;
  code: string;
  severity: string;
  message: string;
  recommends: string;
  threat?: string | null;
  evidence: Record<string, unknown>;
}

export interface Decision {
  obs_id: string;
  at: string | null;
  call_id: string;
  tool: string;
  agent_id: string;
  session_id: string;
  source?: string | null;
  verdict: 'allow' | 'block' | 'escalate_hitl' | 'mutate_and_allow' | string;
  allowed: boolean;
  risk_score: number;
  reason: string;
  mode?: string | null;
  would_block?: boolean | null;
  policy_version?: string | null;
  threat_rules_version?: string | null;
  mutated: boolean;
  approval_state?: string | null;
  action_preview?: string | null;
  findings: Finding[];
}

export interface DecisionPage {
  tenant_id: string;
  decisions: Decision[];
  count: number;
  next_after: string | null;
}

export interface CallDetail {
  tenant_id: string;
  call_id: string;
  decisions: Decision[];
  approval_requests: Record<string, unknown>[];
  approval_decisions: Record<string, unknown>[];
  executions: Record<string, unknown>[];
  outputs: Record<string, unknown>[];
}

export interface StageEvents {
  tenant_id: string;
  events: {
    obs_id: string;
    at: string | null;
    call_id: string;
    tool: string;
    agent_id: string;
    session_id: string;
    verdict: string;
    findings: Finding[];
  }[];
  count: number;
}

export interface ProtectStatus {
  tenant_id: string;
  recent_decisions: number;
  verdicts: Record<string, number>;
  last_decision_at: string | null;
  killed: boolean | null;
  kill_reason: string | null;
  suspended_agents: number;
  suspended_sessions: number;
  policy: { configured: boolean; digest: string | null };
  evidence: { head: { seq: number; segment_hash: string } | null; segments: number };
}

export interface SessionInfo {
  session_id: string;
  objective: string | null;
  decisions: number;
  blocked: number;
  suspended: boolean;
}

export interface AgentInfo {
  agent_id: string;
  sessions: SessionInfo[];
  decisions: number;
  blocked: number;
  escalated: number;
  last_seen: string | null;
  suspended: boolean;
}

export interface Agents {
  tenant_id: string;
  agents: AgentInfo[];
  killed: boolean | null;
}

export interface Mcp {
  tenant_id: string;
  servers: {
    server: string;
    decisions: number;
    verdicts: Record<string, number>;
    tools: Record<string, number>;
    last_seen: string | null;
  }[];
  recent: {
    obs_id: string;
    at: string | null;
    tool: string;
    verdict: string;
    reason: string;
    session_id: string;
    source?: string | null;
  }[];
}

export interface DiscoveryItem {
  kind: string;
  runtime: string;
  name: string;
  status: 'MONITORED' | 'UNMONITORED' | 'UNKNOWN' | 'UNSUPPORTED' | string;
  source: string;
  confidence: number;
  reason: string;
  discovered_at: number;
  details: Record<string, unknown>;
}

export interface Discovery {
  tenant_id: string;
  inventory: {
    discovered_at?: number;
    platform?: string;
    counts?: Record<string, number>;
    items: DiscoveryItem[];
  } | null;
  recorded_at: string | null;
}

export interface Approval {
  request_id: string;
  tenant_id: string;
  agent_id: string;
  session_id: string;
  call_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  action_digest: string;
  reason: string;
  risk_score: number;
  created_at: number;
  expires_at: number;
  state: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled' | string;
  outcome?: Record<string, unknown> | null;
}

export interface PolicyView {
  configured: boolean;
  tenant_id: string;
  digest: string | null;
  version: string | null;
  default: string | null;
  loaded_at: number | null;
  last_error: string[] | null;
  roles: Record<
    string,
    {
      allowed_tools: string[];
      forbidden_tools: string[];
      require_approval: string[];
      rate_limits: Record<string, unknown>;
    }
  >;
  agents: Record<string, string[]>;
}

export interface Permissions {
  agent_id: string;
  tenant_id: string;
  enforced: boolean;
  effect?: string;
  roles?: string[];
  allowed_tools?: string[];
  forbidden_tools?: string[];
  require_approval?: string[];
  restricted_arguments?: Record<string, unknown>;
  rate_limits?: Record<string, unknown>;
}

export interface RbacCheck {
  tenant_id: string;
  agent_id: string;
  tool: string;
  verdict: string;
  authorized: boolean;
  reasons: string[];
}

export interface ControlState {
  tenant_id: string;
  killed: boolean;
  kill_reason: string | null;
  kill_actor: string | null;
  killed_at: string | null;
  local_killed: boolean;
  suspended_agents: Record<string, { reason?: string; actor?: string; at?: string }>;
  suspended_sessions: Record<string, { reason?: string; actor?: string; at?: string }>;
}

export interface EvidenceReport {
  ok: boolean;
  tenant_id: string;
  segments_checked: number;
  observations_checked: number;
  unsealed_observations: number;
  purged_segments: number;
  head: { seq: number; segment_hash: string } | null;
  errors: string[];
  warnings: string[];
  verified_at: string;
  sealed_now?: number;
}
