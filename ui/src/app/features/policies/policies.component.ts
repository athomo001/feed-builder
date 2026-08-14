import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';

import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { DestinationsService, PoliciesService } from '../../core/services';
import { Destination, PolicyCreate, PolicySummary, PolicyVersion, roleSatisfies } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';
import { PolicyCreateDialogComponent, PolicyCreateDialogData } from './policy-create-dialog.component';
import { PolicySimulateDialogComponent, PolicySimulateDialogData } from './policy-simulate-dialog.component';

// spec/07-ADMIN-UI-ANGULAR.md "Politicas": listado -> crear borrador ->
// simular -> publicar/rollback con motivo obligatorio -> historial.
@Component({
  selector: 'app-policies',
  standalone: true,
  imports: [DatePipe, MatButtonModule, MatIconModule, MatTableModule, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './policies.component.html',
  styleUrl: './policies.component.scss',
})
export class PoliciesComponent {
  private readonly policiesService = inject(PoliciesService);
  private readonly destinationsService = inject(DestinationsService);
  private readonly dialog = inject(MatDialog);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly summaryColumns = ['policy_id', 'active_version', 'version_count', 'actions'];
  readonly versionColumns = ['version', 'status', 'created_at', 'published_at', 'actions'];

  readonly policies = pollingSignal<PolicySummary[]>(() => this.policiesService.list(), 15000, []);
  readonly selectedPolicyId = signal<string | null>(null);
  readonly versions = signal<PolicyVersion[]>([]);
  readonly destinations = signal<Destination[]>([]);

  readonly canManage = () => roleSatisfies(this.auth.role(), 'policy-admin');

  constructor() {
    this.destinationsService.list().subscribe((list) => this.destinations.set(list));
  }

  select(policyId: string): void {
    this.selectedPolicyId.set(policyId);
    this.refreshVersions(policyId);
  }

  private refreshVersions(policyId: string): void {
    this.policiesService.versions(policyId).subscribe((v) => this.versions.set(v));
  }

  create(): void {
    this.dialog
      .open<PolicyCreateDialogComponent, PolicyCreateDialogData, PolicyCreate | null>(PolicyCreateDialogComponent, {
        data: { destinations: this.destinations() },
      })
      .afterClosed()
      .subscribe((payload) => {
        if (!payload) return;
        this.policiesService.create(payload).subscribe({
          next: (version) => {
            this.notifications.success(`Borrador "${payload.policy_id}" v${version.version} creado.`);
            this.select(payload.policy_id);
            this.policies.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo crear la politica.'),
        });
      });
  }

  simulate(policyId: string): void {
    this.dialog.open<PolicySimulateDialogComponent, PolicySimulateDialogData>(PolicySimulateDialogComponent, {
      data: { policyId },
      width: '640px',
    });
  }

  publish(version: PolicyVersion): void {
    this.confirmService
      .confirm({
        title: 'Publicar version',
        message: `Vas a publicar ${version.policy_id} v${version.version}. Esto reemplaza la version activa de ese destino.`,
        confirmLabel: 'Publicar',
        requireReason: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.policiesService.publish(version.policy_id, version.version, reason).subscribe({
          next: () => {
            this.notifications.success('Version publicada.');
            this.refreshVersions(version.policy_id);
            this.policies.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo publicar.'),
        });
      });
  }

  rollback(version: PolicyVersion): void {
    this.confirmService
      .confirm({
        title: 'Rollback',
        message: `Vas a reactivar ${version.policy_id} v${version.version} sin alterar su contenido.`,
        confirmLabel: 'Rollback',
        requireReason: true,
        danger: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.policiesService.rollback(version.policy_id, version.version, reason).subscribe({
          next: () => {
            this.notifications.success('Rollback aplicado.');
            this.refreshVersions(version.policy_id);
            this.policies.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo hacer rollback.'),
        });
      });
  }
}
