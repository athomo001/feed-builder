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
