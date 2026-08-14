import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';

import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { AlertsService, DeliveriesService, DestinationsService, IngestionService } from '../../core/services';
import { Alert, Destination, IngestionStatus, LedgerEntry, roleSatisfies } from '../../core/models';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';

// spec/07-ADMIN-UI-ANGULAR.md "Overview": KPIs de cabecera + matriz de
// destinos + badges criticos. Polling corto (ver core/polling.ts) en vez
// de SSE/WebSocket -- decision de alcance ya documentada. Panel de alertas
// activas (Entrega 4, spec/09 "Alertas email/webhook") se integra aca en
// vez de agregar una 8va seccion de navegacion (spec/07 fija 7 secciones).
@Component({
  selector: 'app-overview',
  standalone: true,
  imports: [DecimalPipe, RouterLink, MatButtonModule, MatCardModule, MatIconModule, MatTableModule, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './overview.component.html',
  styleUrl: './overview.component.scss',
})
export class OverviewComponent {
  private readonly ingestionService = inject(IngestionService);
  private readonly destinationsService = inject(DestinationsService);
  private readonly deliveriesService = inject(DeliveriesService);
  private readonly alertsService = inject(AlertsService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly destinationColumns = ['destination_id', 'adapter', 'status', 'format'];

  readonly status = pollingSignal<IngestionStatus | null>(() => this.ingestionService.status(), 5000, null);
  readonly destinations = pollingSignal<Destination[]>(() => this.destinationsService.list(), 10000, []);
  readonly deadLetters = pollingSignal<LedgerEntry[]>(() => this.deliveriesService.deadLetters(), 10000, []);
  readonly alerts = pollingSignal<Alert[]>(() => this.alertsService.list({ state: 'firing' }), 15000, []);

  readonly canOperate = () => roleSatisfies(this.auth.role(), 'operator');

  acknowledge(alert: Alert): void {
    this.alertsService.acknowledge(alert.alert_id).subscribe({
      next: () => {
        this.notifications.success('Alerta reconocida.');
        this.alerts.refresh();
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo reconocer la alerta.'),
    });
  }

  readonly ingestionState = computed(() => {
    const s = this.status();
    if (!s) return 'down';
    if (s.paused) return 'paused';
    const age = s.heartbeat_age_seconds;
    if (age === null || age === undefined) return 'down';
    return age < 600 ? 'ok' : 'degraded';
  });

  readonly deadLetterCount = computed(() => this.deadLetters().length);
  readonly activeDestinationCount = computed(() => this.destinations().filter((d) => d.enabled && !d.paused).length);
}
