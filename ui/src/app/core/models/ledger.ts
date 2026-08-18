// Refleja hub/ledger.py LedgerEntry + hub/delivery.py DeliveryState +
// hub/policy.py ReasonCode. Acotado a lo que el ledger ya guarda -- sin
// Canonical Event Store (ver spec/PROJECT-MAP.md).
export type DeliveryState =
  | 'pending'
  | 'sending'
  | 'delivered'
  | 'acknowledged'
  | 'retrying'
  | 'dead_letter'
  | 'skipped'
  | 'revoked'
  | 'expired';

export interface LedgerEntry {
  event_id: string;
  stix_id: string;
  destination_id: string;
  policy_version: number;
  state: DeliveryState;
  reason: string;
  created_at: string;
  updated_at: string;
  error: string | null;
  attempts: number;
}

export function deliveryId(entry: Pick<LedgerEntry, 'event_id' | 'destination_id' | 'policy_version'>): string {
  return `${entry.event_id}::${entry.destination_id}::${entry.policy_version}`;
}

// Refleja GET /deliveries/queue -- cuantos eventos aceptados por politica
// estan esperando turno para un destino con rate_limit_per_minute
// configurado (estado PENDING en el ledger), sin haberse descartado.
export interface QueueDepth {
  destination_id: string;
  pending: number;
}
