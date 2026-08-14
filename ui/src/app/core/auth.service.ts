import { Injectable, computed, signal } from '@angular/core';

import { Role } from './models';

// spec/07-ADMIN-UI-ANGULAR.md "Seguridad frontend": "Prohibido guardar API
// keys de destinos o tokens de servicios externos en localStorage/
// sessionStorage". Por decision del usuario, el token del propio Hub
// tampoco se persiste: vive solo en memoria (Signal) y se pierde al
// refrescar la pagina -- no hay endpoint de login/sesion (Entrega 2 eligio
// API tokens estaticos, no OIDC), asi que el operador pega el token que ya
// genero por su cuenta y declara que rol tiene.
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly tokenSignal = signal<string | null>(null);
  private readonly roleSignal = signal<Role | null>(null);

  readonly token = this.tokenSignal.asReadonly();
  readonly role = this.roleSignal.asReadonly();
  readonly isAuthenticated = computed(() => this.tokenSignal() !== null);

  login(token: string, role: Role): void {
    this.tokenSignal.set(token);
    this.roleSignal.set(role);
  }

  logout(): void {
    this.tokenSignal.set(null);
    this.roleSignal.set(null);
  }
}
