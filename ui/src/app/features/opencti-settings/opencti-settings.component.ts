import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { OpenCTIConnectionTestResult, roleSatisfies } from '../../core/models';
import { OpenCTISettingsService } from '../../core/services';

// spec/07-ADMIN-UI-ANGULAR.md pivot "standalone": la conexion a OpenCTI
// (URL/token/TLS/stream_id) ya no se configura via env vars en el deploy,
// se guarda aca en caliente (hub/opencti_settings_store.py). Un solo
// registro (no una lista, a diferencia de Destinos/Politicas): se carga
// una vez al entrar a la pagina, no via pollingSignal, para no pisar lo
// que el operador esta escribiendo en el formulario con cada tick.
@Component({
  selector: 'app-opencti-settings',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatCardModule, MatCheckboxModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './opencti-settings.component.html',
  styleUrl: './opencti-settings.component.scss',
})
export class OpenCTISettingsComponent {
  private readonly settingsService = inject(OpenCTISettingsService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly loaded = signal(false);
  readonly hasToken = signal(false);
  readonly url = signal('');
  readonly tlsVerify = signal(true);
  readonly streamId = signal('');
  readonly caCertPath = signal('');
  readonly token = signal('');
  readonly testing = signal(false);
  readonly testResult = signal<OpenCTIConnectionTestResult | null>(null);

  // Lectura = viewer (mismo GET del backend); guardar/probar la conexion
  // pide security-admin, igual que crear/editar un destino o un secreto.
  readonly canManage = () => roleSatisfies(this.auth.role(), 'security-admin');

  constructor() {
    this.load();
  }

  private load(): void {
    this.settingsService.get().subscribe({
      next: (settings) => {
        this.hasToken.set(settings.has_token);
        this.url.set(settings.url ?? '');
        this.tlsVerify.set(settings.tls_verify);
        this.streamId.set(settings.stream_id ?? '');
        this.caCertPath.set(settings.ca_cert_path ?? '');
        this.loaded.set(true);
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo cargar la configuracion de OpenCTI.'),
    });
  }

  save(): void {
    const url = this.url().trim();
    if (!url) {
      this.notifications.error('La URL de OpenCTI es obligatoria.');
      return;
    }
    const token = this.token().trim();
    if (!token && !this.hasToken()) {
      this.notifications.error('Hace falta un token la primera vez que se configura OpenCTI.');
      return;
    }
    this.settingsService
      .update({
        url,
        tls_verify: this.tlsVerify(),
        stream_id: this.streamId().trim() || null,
        ca_cert_path: this.caCertPath().trim() || null,
        ...(token ? { token } : {}),
      })
      .subscribe({
        next: (settings) => {
          this.notifications.success('Conexion a OpenCTI guardada.');
          this.hasToken.set(settings.has_token);
          this.token.set(''); // write-only: nunca se re-muestra, ni el propio recien guardado
          this.testResult.set(null);
        },
        error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo guardar la configuracion.'),
      });
  }

  test(): void {
    this.testing.set(true);
    this.testResult.set(null);
    this.settingsService.test().subscribe({
      next: (result) => {
        this.testing.set(false);
        this.testResult.set(result);
        if (result.ok) {
          this.notifications.success('Conexion a OpenCTI exitosa.');
        } else {
          this.notifications.error(result.error ?? 'La conexion a OpenCTI fallo.');
        }
      },
      error: (err) => {
        this.testing.set(false);
        this.notifications.error(err?.error?.detail ?? 'No se pudo probar la conexion.');
      },
    });
  }
}
