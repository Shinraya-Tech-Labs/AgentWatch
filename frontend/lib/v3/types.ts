// Types mirroring the PANOPTIS v3 API (panoptis/api/v3.py). The frontend renders the
// canonical v3 model as served; it never re-derives interpretations of its own.

export type EventKind =
  | 'MODEL_INVOCATION'
  | 'TOOL_INVOCATION'
  | 'RETRIEVAL'
  | 'MEMORY_ACCESS'
  | 'MESSAGE'
  | 'DELEGATION'
  | 'TRANSFORMATION'
  | 'STATE_MUTATION'
  | 'EXTERNAL_IO'
  | 'EXTERNAL_INPUT'
  | 'SYNCHRONIZATION'
  | 'OPERATION'
  | 'LIFECYCLE'
  | 'FAILURE'
  | 'RECOVERY'
  | 'UNKNOWN';

export interface Confidence {
  value: number;
  basis: string;
  calibrated: boolean;
}

export interface ArtifactRef {
  /** content identity (equal bytes, equal id); not information identity (ADR-0017) */
  artifact_id: string;
  role: string;
  label: string | null;
  /** sensor-declared instance id of a produced value */
  instance?: string;
  /** sensor-declared sources of a consumed value */
  sources?: string[];
}

export interface CEvent {
  event_id: string;
  interp_id: string;
  normalizer: string;
  derived_from: string[];
  kind: EventKind;
  operation: string;
  status: 'OK' | 'ERROR' | 'TIMEOUT' | 'CANCELLED' | 'UNKNOWN';
  actor: string | null;
  object: string | null;
  facets: string[];
  inputs: ArtifactRef[];
  outputs: ArtifactRef[];
  effects: { kind: string; target: string; target_type: string }[];
  time: {
    start: string | null;
    end: string | null;
    basis: string;
    duration_ms: number | null;
    ordering_key: string;
  };
  source_ids: [string, string][];
  parents: { relation: string; key_space: string; value: string }[];
  run_key: [string, string] | null;
  run_id: string | null;
  run_basis: string | null;
  error: Record<string, unknown> | null;
  resources: Record<string, number>;
  attributes: Record<string, unknown>;
  confidence: { observation: Confidence; attribution: Confidence };
  missing: string[];
}

export interface Run {
  run_id: string;
  name: string | null;
  status: string;
  status_basis: string;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  event_count: number;
  error_events: number;
  kinds: Record<string, number>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  attributes: Record<string, unknown>;
  fingerprint: Record<string, unknown>;
  system_version: string;
  declared_links: number;
  resolved_links: number;
  completeness: number;
  unresolved_links: { event_id: string; relation: string; key_space: string; value: string }[];
}

export type View = 'EXECUTION' | 'INFORMATION' | 'CAUSAL';

export interface Relation {
  rel_id: string;
  view: View;
  type: string;
  tail: string[];
  head: string[];
  basis: string;
  evidence_class: string | null;
  confidence: number;
  run_id: string | null;
  evidence: string[];
  derived_by: string;
  attributes: Record<string, unknown>;
}

export interface NodeDesc {
  node: string;
  /** 'instance': an information instance (inst:<event>/o<k>, /o<k>/i<j>, /in<k>) */
  type: 'event' | 'instance' | 'artifact' | 'entity';
  label: string;
  content_id?: string | null;
  /** how many instances in the run carry the same bytes (they are still distinct values) */
  same_content_instances?: number;
  producer_event?: string | null;
  consumer_event?: string | null;
  role?: string | null;
  erased?: boolean;
  /** entities: how the key was resolved; DECLARED_ALIAS merges only user-declared aliases */
  kind?: string;
  event_count?: number;
  runs?: number;
  resolution?: 'EXACT_KEY' | 'DECLARED_ALIAS';
  aliases?: string[];
  alias_of?: string;
  actor?: string | null;
  status?: string;
  start?: string | null;
  run_id?: string | null;
  preview?: string | null;
  roles?: string[];
}

export interface GraphResponse {
  run_id: string;
  view: string;
  nodes: NodeDesc[];
  relations: Relation[];
  stats: Record<string, unknown>;
}

export interface MotifInstance {
  record_id: string;
  motif_id: string;
  motif_name: string;
  run_id: string;
  events: string[];
  artifacts: string[];
  confidence: number;
  explanation: string;
  measures: Record<string, unknown>;
  maturity: string;
}

export interface RunDetail {
  run: Run;
  events: CEvent[];
  graph: Record<string, unknown>;
  actors: Record<string, number>;
  tools: Record<string, number>;
  models: Record<string, number>;
  failures: { event_id: string; label: string; error: Record<string, unknown> | null }[];
  retries: number;
  missing_facts: Record<string, number>;
  information: Record<string, number>;
  tree: { depth: number; event_id: string }[];
  motif_instances: MotifInstance[];
  profile: { features: Record<string, number>; motif_counts: Record<string, number> } | null;
  information_evidence?: InformationEvidence | null;
}

export interface ObservationDoc {
  obs_id: string;
  source_kind: string;
  sensor: { sensor_type: string; sensor_version: string; instance_id: string };
  observed_at: string | null;
  received_at: string;
  payload_sha256: string;
  declared_ids: Record<string, string>;
  redaction: Record<string, unknown> | null;
  segment_id: string | null;
  /** 'erased': the data subject was crypto-shredded; the payload is gone for good */
  encoding?: 'plain' | 'aes-gcm' | 'erased';
  payload: unknown;
}

/** Attributes every INFORMATION relation carries (ADR-0017). */
export interface InfoEvidenceAttrs {
  evidence_type?: string;
  strength?: 'STRONG' | 'MEDIUM' | 'WEAK' | 'NONE';
  mode?: 'HIGH_FIDELITY' | 'BEST_EFFORT';
  resolution?: 'RESOLVED' | 'AMBIGUOUS' | 'UNRESOLVED';
  alternatives?: string[][];
  certain?: string[];
  source_event?: string;
  target_event?: string;
}

export interface AmbiguousProvenance {
  record_id: string;
  target: string;
  target_event: string;
  resolution_status: 'AMBIGUOUS';
  candidates: {
    node: string;
    source_event: string | null;
    evidence_type: string | null;
    containment: number | null;
    alternatives: string[][];
  }[];
  certain_sources: string[];
}

export interface InformationEvidence {
  relations_by_evidence_type: Record<string, number>;
  relations_by_strength: Record<string, number>;
  links_by_mode: Record<string, number>;
  high_fidelity_share: number | null;
  consumed_values: Record<string, number>;
  ambiguous_values: number;
}

export interface ProvenanceEvidence {
  run_id: string;
  summary: InformationEvidence | null;
  ambiguous: AmbiguousProvenance[];
}

export interface EventDetail {
  event: CEvent;
  evidence: ObservationDoc[];
  incoming: (Relation & { other: NodeDesc })[];
  outgoing: (Relation & { other: NodeDesc })[];
  motifs: MotifInstance[];
}

export interface Divergence {
  type: string;
  operation: string;
  a_event: { event_id: string; label: string; status: string } | null;
  b_event: { event_id: string; label: string; status: string } | null;
  reasons: string[];
  cone: {
    events_in_cone: number;
    events_total: number;
    latency_share: number | null;
    token_share: number | null;
    interpretation: string;
  } | null;
}

export interface CompareResult {
  run_a: Partial<Run>;
  run_b: Partial<Run>;
  identical_structure: boolean;
  earliest_divergence: Divergence | null;
  structural: {
    only_in_a: { signature: string; count: number }[];
    only_in_b: { signature: string; count: number }[];
    edges_only_in_a: string[];
    edges_only_in_b: string[];
    alignment_similarity: number;
  };
  resources: Record<string, { a: number; b: number; delta: number; ratio: number | null }>;
  information: Record<string, { a: number; b: number; delta: number }>;
  motifs: Record<string, { a: number; b: number }>;
  fingerprint: {
    same_system_version: boolean;
    a: Record<string, unknown>;
    b: Record<string, unknown>;
  };
  method: string;
}

export interface Capability {
  name: string;
  version: string;
  maturity: 'EXPERIMENTAL' | 'VALIDATED' | 'PRODUCTION';
  description: string;
  evidence: string[];
}
