import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../environments/environment';
import { Role } from './models';

export type AuthMethod = 'token' | 'session' | null;

// spec/07-ADMIN-UI-ANGULAR.md "Seguridad frontend": "Prohibido guardar API
// keys de destinos o tokens de servicios externos en localStorage/
// sessionStorage". El token de API (Entrega 2) sigue en memoria unicamente
// (Signal, se pierde al refrescar). Entrega 5 agrega un segundo metodo,
// OIDC/SSO: ese login usa una cookie de sesion HttpOnly manejada por el
// servidor (nunca visible a JS), asi que aca solo se guarda el ROL
// resuelto -- `checkExistingSession()` la detecta al arrancar via
// `GET /auth/whoami` en vez de guardar nada persistente del lado del cliente.
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  private readonly tokenSignal = signal<string | null>(null);
  private readonly roleSignal = signal<Role | null>(null);
  private readonly methodSignal = signal<AuthMethod>(null);

  readonly token = this.tokenSignal.asReadonly();
  readonly role = this.roleSignal.asReadonly();
  readonly method = this.methodSignal.asReadonly();
  readonly isAuthenticated = computed(() => this.tokenSignal() !== null || this.methodSignal() === 'session');

  login(token: string, role: Role): void {
    this.tokenSignal.set(token);
    this.roleSignal.set(role);
    this.methodSignal.set('token');
  }

  private setSession(role: Role): void {
    this.tokenSignal.set(null);
    this.roleSignal.set(role);
    this.methodSignal.set('session');
  }

  logout(): void {
    const wasSession = this.methodSignal() === 'session';
    this.tokenSignal.set(null);
    this.roleSignal.set(null);
    this.methodSignal.set(null);
    if (wasSession) {
      this.http.post(`${environment.apiBaseUrl}/auth/logout`, {}, { withCredentials: true }).subscribe();
    }
  }

  /** Se llama una vez al arrancar la app (`provideAppInitializer`, ver
   * app.config.ts): si ya hay una cookie de sesion OIDC valida, la detecta
   * sin que el operador tenga que loguearse de nuevo. Sin sesion activa,
   * no hace nada -- se queda en /login, mismo comportamiento de siempre. */
  async checkExistingSession(): Promise<void> {
    try {
      const result = await firstValueFrom(
        this.http.get<{ token_id: string; role: Role }>(`${environment.apiBaseUrl}/auth/whoami`, {
          withCredentials: true,
        }),
      );
      this.setSession(result.role);
    } catch {
      // Sin sesion activa: comportamiento normal, no es un error a mostrar.
    }
  }
}
