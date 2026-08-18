import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { environment } from '../../environments/environment';
import { Role } from './models';

export type AuthMethod = 'token' | 'session' | null;

// spec/07-ADMIN-UI-ANGULAR.md "Seguridad frontend": "Prohibido guardar API
// keys de destinos o tokens de servicios externos en localStorage/
// sessionStorage". Entrega 5 agrega un segundo metodo, OIDC/SSO: ese login
// usa una cookie de sesion HttpOnly manejada por el servidor (nunca
// visible a JS), asi que aca solo se guarda el ROL resuelto --
// `checkExistingSession()` la detecta al arrancar via `GET /auth/whoami`.
//
// EXCEPCION deliberada a la regla de arriba, pedida explicitamente por el
// operador (2026-08-15): el token de API SI se persiste en
// `sessionStorage` (sobrevive a un refresh, se borra al cerrar la
// pestana), para no tener que re-pegarlo en cada reload durante uso/prueba
// local. Esto reintroduce el riesgo que esa regla busca evitar (un XSS en
// la UI podria leer sessionStorage y robar el token persistido) -- no
// revertir esto sin que el operador lo pida de nuevo con ese trade-off en
// mente, y reconsiderar antes de usar este build en un despliegue real
// expuesto a usuarios no confiables.
const SESSION_STORAGE_TOKEN_KEY = 'hub_admin_token';
const SESSION_STORAGE_ROLE_KEY = 'hub_admin_role';

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
    sessionStorage.setItem(SESSION_STORAGE_TOKEN_KEY, token);
    sessionStorage.setItem(SESSION_STORAGE_ROLE_KEY, role);
  }

  private setSession(role: Role): void {
    this.tokenSignal.set(null);
    this.roleSignal.set(role);
    this.methodSignal.set('session');
  }

  /** Cambia SOLO la etiqueta de rol mostrada en esta pestana (que items/
   * botones se ven), pedido explicitamente para explorar la UI sin volver
   * a loguearse (2026-08-15). NO otorga permisos reales: el servidor
   * siempre revalida el rol verdadero del token en cada request (spec/08
   * "la autorizacion real siempre la aplica el Admin API") -- una accion
   * que el token no puede hacer de verdad sigue devolviendo 403 aunque
   * acá se muestre "security-admin". Se persiste en sessionStorage
   * (misma excepcion documentada arriba) para que el reload no la pierda. */
  setDisplayRole(role: Role): void {
    this.roleSignal.set(role);
    sessionStorage.setItem(SESSION_STORAGE_ROLE_KEY, role);
  }

  logout(): void {
    const wasSession = this.methodSignal() === 'session';
    this.tokenSignal.set(null);
    this.roleSignal.set(null);
    this.methodSignal.set(null);
    sessionStorage.removeItem(SESSION_STORAGE_TOKEN_KEY);
    sessionStorage.removeItem(SESSION_STORAGE_ROLE_KEY);
    if (wasSession) {
      this.http.post(`${environment.apiBaseUrl}/auth/logout`, {}, { withCredentials: true }).subscribe();
    }
  }

  /** Se llama una vez al arrancar la app (`provideAppInitializer`, ver
   * app.config.ts). Primero intenta restaurar un token pegado a mano en
   * una pestana anterior (ver excepcion documentada arriba); si no hay
   * ninguno, cae al chequeo de sesion OIDC via `GET /auth/whoami`. Sin
   * ninguna de las dos, entra igual con un rol por defecto -- EXCEPCION
   * deliberada, pedida explicitamente por el operador (2026-08-17): el
   * Admin API (`hub/api/auth.py::require_role`) ya no exige un Bearer/sesion
   * valido, asi que forzar el login en el cliente ya no protegia nada, solo
   * agregaba un paso manual (generar y pegar un token) sin beneficio real.
   * No revertir a "se queda en /login" sin que el operador lo pida de nuevo. */
  async checkExistingSession(): Promise<void> {
    const storedToken = sessionStorage.getItem(SESSION_STORAGE_TOKEN_KEY);
    const storedRole = sessionStorage.getItem(SESSION_STORAGE_ROLE_KEY) as Role | null;
    if (storedToken && storedRole) {
      this.tokenSignal.set(storedToken);
      this.roleSignal.set(storedRole);
      this.methodSignal.set('token');
      return;
    }

    try {
      const result = await firstValueFrom(
        this.http.get<{ token_id: string; role: Role }>(`${environment.apiBaseUrl}/auth/whoami`, {
          withCredentials: true,
        }),
      );
      this.setSession(result.role);
    } catch {
      this.setSession('security-admin');
    }
  }
}
