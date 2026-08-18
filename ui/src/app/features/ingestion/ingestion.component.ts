import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { AuthService } from '../../core/auth.service';
import { ingestionHealthLabel, ingestionHealthState } from '../../core/ingestion-status';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { IngestionService } from '../../core/services';
import { IngestionStatus, roleSatisfies } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';

// spec/07-ADMIN-UI-ANGULAR.md "OpenCTI / Ingesta". Rewind sin preview de
// volumen (gap #2 documentado en el plan/CHANGELOG): no hay forma barata
// de contar eventos entre dos cursores sin re-consultar OpenCTI extenso,
// asi que se pide motivo y se muestra cursor actual vs solicitado nomas.
@Component({
  selector: 'app-ingestion',
  standalone: true,
  imports: [DecimalPipe, FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './ingestion.component.html',
  styleUrl: './ingestion.component.scss',
})
export class IngestionComponent {
  private readonly ingestionService = inject(IngestionService);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly status = pollingSignal<IngestionStatus | null>(() => this.ingestionService.status(), 5000, null);
  readonly rewindCursor = signal('');

  readonly healthState = () => ingestionHealthState(this.status());
  readonly healthLabel = () => ingestionHealthLabel(this.healthState());

  readonly canOperate = () => roleSatisfies(this.auth.role(), 'operator');
  readonly canRewind = () => roleSatisfies(this.auth.role(), 'security-admin');

  pause(): void {
    this.confirmService
      .confirm({ title: 'Pausar ingestion', message: 'El Hub dejara de procesar el Live Stream hasta que reanudes.' })
      .subscribe(({ confirmed }) => {
        if (!confirmed) return;
        this.ingestionService.pause().subscribe({
          next: () => {
            this.notifications.success('Ingestion pausada.');
            this.status.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo pausar.'),
        });
      });
  }

  resume(): void {
    this.ingestionService.resume().subscribe({
      next: () => {
        this.notifications.success('Ingestion reanudada.');
        this.status.refresh();
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo reanudar.'),
    });
  }

  reconcile(): void {
    this.ingestionService.reconcile().subscribe({
      next: () => {
        this.notifications.success('Reconciliacion solicitada: se ejecuta en el proximo ciclo del loop de ingestion.');
        this.status.refresh();
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo solicitar la reconciliacion.'),
    });
  }

  rewind(): void {
    const cursor = this.rewindCursor().trim();
    if (!cursor) {
      this.notifications.error('Ingresa el cursor (id de evento SSE) al que queres rebobinar.');
      return;
    }
    this.confirmService
      .confirm({
        title: 'Rebobinar cursor',
        message: `Cursor actual: "${this.status()?.cursor_value ?? 'desconocido'}". Vas a rebobinar a "${cursor}". Esto puede reprocesar eventos ya vistos; el ledger no se borra.`,
        confirmLabel: 'Rebobinar',
        requireReason: true,
        danger: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.ingestionService.rewind(cursor, reason).subscribe({
          next: () => {
            this.notifications.success('Rewind solicitado: se aplica en el proximo ciclo del loop de ingestion.');
            this.status.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo solicitar el rewind.'),
        });
      });
  }
}
