import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { catchError, of } from 'rxjs';

import { AuthService } from '../../core/auth.service';
import { ALL_ROLES, Role } from '../../core/models';
import { environment } from '../../../environments/environment';

// spec/07-ADMIN-UI-ANGULAR.md "Seguridad frontend": el token nunca se
// persiste (ver hub/api/auth.service.ts... hub/api/token_store.py del
// backend genera el token con un script manual, README.md 14.2). Esta
// pantalla solo lo pega en memoria; no hay endpoint de login/sesion.
@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent {
  private readonly auth = inject(AuthService);
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  readonly roles = ALL_ROLES;
  readonly token = signal('');
  readonly role = signal<Role>('viewer');
  readonly checking = signal(false);
  readonly error = signal<string | null>(null);

  submit(): void {
    const token = this.token().trim();
    if (!token) {
      this.error.set('Pega un token valido.');
      return;
    }
    this.checking.set(true);
    this.error.set(null);

    // No hay endpoint "whoami": se valida el token con una llamada de
    // solo lectura minima (requiere rol viewer, el mas bajo) antes de
    // aceptar el login. El rol declarado en el formulario se confia tal
    // cual -- es el mismo rol que el operador ya eligio al generar el
    // token (README.md 14.2).
    this.http
      .get(`${environment.apiBaseUrl}/destinations`, { headers: { Authorization: `Bearer ${token}` } })
      .pipe(
        catchError(() => {
          this.error.set('Token invalido, revocado o expirado.');
          return of(null);
        }),
      )
      .subscribe((result) => {
        this.checking.set(false);
        if (result !== null) {
          this.auth.login(token, this.role());
          this.router.navigateByUrl('/overview');
        }
      });
  }
}
