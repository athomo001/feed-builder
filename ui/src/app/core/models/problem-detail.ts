// Refleja hub/errors.py ProblemDetail (RFC 9457, spec/08-API-SECURITY.md
// "Formato de errores").
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance: string;
  error_code?: string | null;
  event_id?: string | null;
  delivery_id?: string | null;
  correlation_id?: string | null;
}
