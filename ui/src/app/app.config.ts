import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';

import { routes } from './app.routes';
import { AuthService } from './core/auth.service';
import { authInterceptor } from './core/auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideAnimationsAsync(),
    // Entrega 5 "OIDC/SSO": detecta una sesion existente (cookie HttpOnly)
    // antes de que se evaluen los guards de ruta, para no mandar a un
    // operador con sesion SSO activa de vuelta a /login en cada refresh.
    provideAppInitializer(() => inject(AuthService).checkExistingSession()),
  ],
};
