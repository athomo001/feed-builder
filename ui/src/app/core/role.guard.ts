import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { AuthService } from './auth.service';
import { Role, roleSatisfies } from './models';

// spec/08-API-SECURITY.md roles; spec/07 "Guards: CanActivate basados en
// los roles locales". Esto es solo UX (ocultar/redirigir en el cliente) --
// el servidor revalida el rol exacto en cada endpoint (spec/08 API5), asi
// que este guard nunca es el unico control real.
export function requireRole(minRole: Role): CanActivateFn {
  return () => {
    const auth = inject(AuthService);
    const router = inject(Router);

    if (!auth.isAuthenticated()) {
      return router.parseUrl('/login');
    }
    if (!roleSatisfies(auth.role(), minRole)) {
      return router.parseUrl('/forbidden');
    }
    return true;
  };
}

// Desde que `AuthService.checkExistingSession` entra sola con un rol por
// defecto (2026-08-17, ver ese metodo), casi cualquier request ya llega
// autenticado -- pero la ruta /login en si no tenia guard, asi que abrirla
// directo (un tab vieja, un bookmark) seguia mostrando el formulario aunque
// ya hubiera sesion. Este guard saca de /login a quien ya esta autenticado.
export const redirectIfAuthenticated: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.isAuthenticated() ? router.parseUrl('/') : true;
};
