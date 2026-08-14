// Refleja hub/api/audit_store.py AuditEntry.
export interface AuditEntry {
  audit_id: string;
  actor_token_id: string | null;
  actor_role: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reason: string | null;
  result: 'success' | 'failure';
  correlation_id: string | null;
  created_at: string;
}

export interface AuditFilters {
  actor_token_id?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}
