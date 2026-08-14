import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';

import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { DestinationsService } from '../../core/services';
import { Destination, DestinationCreate, roleSatisfies } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';
import { DestinationFormDialogComponent, DestinationFormDialogData } from './destination-form-dialog.component';

// spec/07-ADMIN-UI-ANGULAR.md "Destinos": tabla densa + acciones
// [Probar Conexion]/[Pausar]/[Editar]. Escritura requiere security-admin,
// pausar/reanudar requiere operator (spec/08 roles) -- el server ya lo
// aplica, aca solo se ocultan botones que igual fallarian con 403.
@Component({
  selector: 'app-destinations',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatTableModule, MatTooltipModule, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './destinations.component.html',
  styleUrl: './destinations.component.scss',
})
export class DestinationsComponent {
  private readonly destinationsService = inject(DestinationsService);
  private readonly dialog = inject(MatDialog);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly columns = ['destination_id', 'name', 'adapter', 'status', 'format', 'actions'];
  readonly destinations = pollingSignal<Destination[]>(() => this.destinationsService.list(), 15000, []);

  readonly canManage = () => roleSatisfies(this.auth.role(), 'security-admin');
  readonly canOperate = () => roleSatisfies(this.auth.role(), 'operator');

  create(): void {
    this.dialog
      .open<DestinationFormDialogComponent, DestinationFormDialogData, DestinationCreate | null>(
        DestinationFormDialogComponent,
        { data: { mode: 'create' } },
      )
      .afterClosed()
      .subscribe((payload) => {
        if (!payload) return;
        this.destinationsService.create(payload).subscribe({
          next: () => {
            this.notifications.success(`Destino "${payload.destination_id}" creado.`);
            this.destinations.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo crear el destino.'),
        });
      });
  }

  edit(destination: Destination): void {
    this.dialog
      .open<DestinationFormDialogComponent, DestinationFormDialogData, DestinationCreate | null>(
        DestinationFormDialogComponent,
        { data: { mode: 'edit', destination } },
      )
      .afterClosed()
      .subscribe((payload) => {
        if (!payload) return;
        this.destinationsService.update(destination.destination_id, payload).subscribe({
          next: () => {
            this.notifications.success('Destino actualizado.');
            this.destinations.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo actualizar el destino.'),
        });
      });
  }

  test(destination: Destination): void {
    this.destinationsService.test(destination.destination_id).subscribe({
      next: (result) => {
        if (result.healthy) {
          this.notifications.success(`"${destination.destination_id}": conexion OK (payload sintetico).`);
        } else {
          this.notifications.error(`"${destination.destination_id}" fallo: ${result.errors.join('; ')}`);
        }
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo probar el destino.'),
    });
  }

  togglePause(destination: Destination): void {
    const action = destination.paused ? 'reanudar' : 'pausar';
    this.confirmService
      .confirm({
        title: `${action === 'pausar' ? 'Pausar' : 'Reanudar'} destino`,
        message: `Vas a ${action} "${destination.destination_id}". Confirmalo para continuar.`,
        confirmLabel: action === 'pausar' ? 'Pausar' : 'Reanudar',
        danger: action === 'pausar',
      })
      .subscribe(({ confirmed }) => {
        if (!confirmed) return;
        const call = destination.paused
          ? this.destinationsService.resume(destination.destination_id)
          : this.destinationsService.pause(destination.destination_id);
        call.subscribe({
          next: () => {
            this.notifications.success(`Destino ${action === 'pausar' ? 'pausado' : 'reanudado'}.`);
            this.destinations.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? `No se pudo ${action} el destino.`),
        });
      });
  }
}
