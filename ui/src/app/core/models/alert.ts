// Refleja hub/alerting_store.py Alert (mismos nombres de campo que el
// Admin API). Entrega 4 (spec/09 "Alertas email/webhook").
export type AlertSeverity = 'info' | 'warning' | 'critical';
export type AlertState = 'firing' | 'acknowledged' | 'resolved';

export interface Alert {
  alert_id: string;
  condition: string;
  severity: AlertSeverity;
  state: AlertState;
  component: string;
  resource_id: string;
  observed_value: string | null;
  first_seen_at: string;
  last_seen_at: string;
  last_notified_at: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}
