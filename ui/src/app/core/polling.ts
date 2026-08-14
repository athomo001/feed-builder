import { Signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { Observable, Subject, catchError, interval, merge, of, startWith, switchMap } from 'rxjs';

// spec/07-ADMIN-UI-ANGULAR.md "Real-time: ... con fallback a polling corto
// si la conexion persistente falla". Decision de alcance de esta Entrega:
// sin SSE/WebSocket todavia (ver spec/PROJECT-MAP.md) -- se parte directo
// de polling corto contra el REST plano existente.
//
// `refresh()` fuerza una re-consulta inmediata (por ejemplo, justo despues
// de una accion que muta datos) en vez de esperar al proximo tick del
// intervalo -- sin esto, una accion exitosa podia tardar hasta el intervalo
// completo en reflejarse en pantalla.
export type PollingSignal<T> = Signal<T> & { refresh: () => void };

// Debe llamarse en contexto de inyeccion (campo de clase o constructor de
// un componente/servicio), igual que cualquier uso de `toSignal`.
export function pollingSignal<T>(fetchFn: () => Observable<T>, intervalMs: number, initialValue: T): PollingSignal<T> {
  const trigger$ = new Subject<void>();
  const poll$ = merge(interval(intervalMs), trigger$).pipe(
    startWith(0),
    switchMap(() => fetchFn().pipe(catchError(() => of(initialValue)))),
  );
  const sig = toSignal(poll$, { initialValue }) as PollingSignal<T>;
  sig.refresh = () => trigger$.next();
  return sig;
}
