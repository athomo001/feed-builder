// Refleja hub/destinations_store.py Destination + hub/api/schemas.py
// DestinationCreate/Update (mismos nombres de campo que el Admin API).
// Entrega 4 (spec/09 "Integraciones") agrego las 6 variantes nuevas.
export type AdapterType =
  | 'txt_feed'
  | 'http_push'
  | 'csv_feed'
  | 'mikrotik_rsc'
  | 'wazuh_cdb'
  | 'stix_bundle_feed'
  | 'qradar_reference_set'
  | 'taxii2';

export interface RetryPolicy {
  max_attempts: number;
  backoff: 'exponential-jitter';
}

export interface Destination {
  destination_id: string;
  name: string;
  adapter: AdapterType;
  enabled: boolean;
  paused: boolean;
  endpoint: string | null;
  credential_ref: string | null;
  format: string;
  format_options: Record<string, unknown>;
  capacity: Record<string, unknown>;
  supports_delete: boolean;
  delete_strategy: string | null;
  timeout_seconds: number;
  retry: RetryPolicy;
  created_at: string;
  updated_at: string;
}

export interface DestinationCreate {
  destination_id: string;
  name: string;
  adapter: AdapterType;
  enabled?: boolean;
  endpoint?: string | null;
  credential_ref?: string | null;
  format?: string;
  format_options?: Record<string, unknown>;
  capacity?: Record<string, unknown>;
  supports_delete?: boolean;
  delete_strategy?: string | null;
  timeout_seconds?: number;
  retry?: RetryPolicy;
}

export type DestinationUpdate = Partial<Omit<DestinationCreate, 'destination_id' | 'adapter'>>;

export interface DestinationTestResult {
  destination_id: string;
  synthetic: true;
  errors: string[];
  healthy: boolean;
}
