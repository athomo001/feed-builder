import { IngestionStatus } from './models';

export type IngestionHealthState = 'ok' | 'degraded' | 'paused' | 'down' | 'unknown';

// Mismo umbral que hub/alert_rules.py::evaluate_opencti_disconnected
// (max_age_seconds default).
//
// El heartbeat NO alcanza solo para decidir "conectado o no": OpenCTI manda
// keep-alives de la conexion SSE que hub/service.py::listen_live_stream
// registra como sse_event y refrescan el heartbeat (write_heartbeat corre
// para CUALQUIER sse_event, no solo los que traen un IOC real) -- eso lo
// hacia parpadear entre OK y DOWN sin que cambiara nada real (encontrado
// 2026-08-18: heartbeat fresco en un momento, viejo un minuto despues, con
// cursor_value en null todo el tiempo). El dato que no fluctua es
// cursor_value: solo se actualiza cuando llega un evento con `id` real, asi
// que "nunca tuvo cursor" es una senal estable de "nunca llego un IOC real",
// mas alla de como este el heartbeat en un instante dado.
const HEARTBEAT_STALE_SECONDS = 120;

export function ingestionHealthState(status: IngestionStatus | null): IngestionHealthState {
  if (!status) return 'unknown';
  if (status.paused) return 'paused';
  if (status.heartbeat_age_seconds === null || status.heartbeat_age_seconds === undefined) return 'unknown';
  if (status.heartbeat_age_seconds > HEARTBEAT_STALE_SECONDS) return 'down'; // el proceso ni siquiera esta en loop normal
  return status.cursor_value ? 'ok' : 'degraded'; // vivo, pero sin haber recibido nunca un evento real
}

// Etiquetas deliberadamente tajantes (sin matices tipo "funciona pero no
// funciona"): activa, degradada, pausada, caida, o sin datos todavia -- un
// operador tiene que poder leer el estado de un vistazo, sin tener que
// interpretar la redaccion.
export function ingestionHealthLabel(state: IngestionHealthState): string {
  switch (state) {
    case 'paused':
      return 'Ingesta pausada';
    case 'down':
      return 'Sin conexion con OpenCTI';
    case 'degraded':
      return 'Conectado, pero nunca recibio un evento real';
    case 'ok':
      return 'Ingesta activa';
    default:
      return 'Sin datos todavia';
  }
}
