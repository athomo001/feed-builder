import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';
import { NotificationService } from './notification.service';
import { ProblemDetail } from './models';

// spec/07-ADMIN-UI-ANGULAR.md "HTTP: interceptor global para inyeccion del
// header X-Correlation-Id y captura centralizada de errores 401/403/500".
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const notifications = inject(NotificationService);
  const router = inject(Router);

  const headers: Record<string, string> = { 'X-Correlation-Id': crypto.randomUUID() };
  const token = auth.token();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // withCredentials: true en toda request -- la sesion OIDC (Entrega 5)
  // viaja en una cookie HttpOnly, no en memoria; el navegador solo la
  // adjunta si el request lo pide explicitamente. Inofensivo para el flujo
  // de token pegado (no hay cookie que enviar en ese caso).
  return next(req.clone({ setHeaders: headers, withCredentials: true })).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse) {
        const problem = (err.error ?? null) as ProblemDetail | null;

        if (err.status === 401) {
          auth.logout();
          notifications.error('Token invalido, revocado o expirado. Volve a pegar un token valido.');
          router.navigateByUrl('/login');
        } else if (err.status === 403) {
          notifications.error(problem?.detail ?? 'No tenes permiso para esta accion (rol insuficiente).');
        } else if (err.status >= 500) {
          const cid = problem?.correlation_id ? ` (correlation_id: ${problem.correlation_id})` : '';
          notifications.error(`Error del servidor${cid}: ${problem?.detail ?? err.message}`);
        }
      }
      return throwError(() => err);
    }),
  );
};
