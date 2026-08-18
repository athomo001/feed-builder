// Refleja hub/policy_store.py PolicyVersion + hub/policy_simulation.py.
export type PolicyStatus = 'draft' | 'published' | 'superseded' | 'rolled_back';

export interface AllowedIOC {
  family: string;
  subtypes: string[];
}

export interface PolicyVersion {
  policy_id: string;
  version: number;
  destination_id: string;
  allowed_iocs: AllowedIOC[];
  ttl_days: Record<string, number>;
  // subtype -> cantidad maxima vigente (0/ausente = sin tope propio, usa el
  // default parejo del destino). El mas viejo se descarta cuando se llena el
  // cupo, independiente de si el TTL todavia no vencio.
  max_records: Record<string, number>;
  status: PolicyStatus;
  created_at: string;
  published_at: string | null;
}

export interface PolicySummary {
  policy_id: string;
  active_version: number | null;
  version_count: number;
}

export interface PolicyCreate {
  policy_id: string;
  destination_id: string;
  allowed_iocs: AllowedIOC[];
  ttl_days?: Record<string, number>;
  max_records?: Record<string, number>;
}

export interface SimulationExample {
  stix_id: string;
  family: string;
  subtype: string;
  outcome: string;
  reason: string;
}

export interface SimulationTally {
  accepted: number;
  rejected: number;
  revoked: number;
}

export interface SimulationReport {
  policy_id: string;
  candidate_version: number;
  sample_size: number;
  before: SimulationTally;
  after: SimulationTally & { examples: SimulationExample[] };
  delta_pct: number | null;
  threshold_alert: boolean;
}
