// Refleja hub/api/routers/ingestion.py (status ad-hoc) y hub/ingestion_control.py.
export interface IngestionStatus {
  source_id: string;
  paused: boolean;
  reconcile_requested: boolean;
  rewind_pending: boolean;
  cursor_value: string | null;
  cursor_updated_at: string | null;
  heartbeat_age_seconds: number | null;
}

export interface IngestionControl {
  source_id: string;
  paused: boolean;
  reconcile_requested: boolean;
  rewind_to_cursor: string | null;
  rewind_reason: string | null;
  updated_at: string;
}
