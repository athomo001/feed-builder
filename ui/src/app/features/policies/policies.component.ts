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

  readonly summaryColumns = ['policy_id', 'active_version', 'version_count', 'destination_ids', 'actions'];
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
        width: '960px',
        maxWidth: '95vw',
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

  // "Editar" real, pedido explicitamente por el operador (2026-08-18): en un
  // solo paso, crea un draft con los cambios y lo publica de una -- sin la
  // danza manual de crear + simular + publicar por separado. Sigue pasando
  // por el mismo gate de cambio de volumen significativo que un publish
  // cualquiera (doPublish), asi que no se pierde esa red de seguridad.
  editPolicy(summary: PolicySummary): void {
    this.policiesService.versions(summary.policy_id).subscribe((versions) => {
      const base = versions.find((v) => v.version === summary.active_version) ?? versions[versions.length - 1];
      if (!base) return;
      this.dialog
        .open<PolicyCreateDialogComponent, PolicyCreateDialogData, PolicyCreate | null>(PolicyCreateDialogComponent, {
          data: {
            destinations: this.destinations(),
            edit: {
              policyId: base.policy_id,
              destinationIds: summary.destination_ids,
              allowedIocs: base.allowed_iocs,
              ttlDays: base.ttl_days,
              maxRecords: base.max_records,
            },
          },
          width: '960px',
          maxWidth: '95vw',
        })
        .afterClosed()
        .subscribe((payload) => {
          if (!payload) return;
          this.policiesService.create(payload).subscribe({
            next: (version) => this.doPublish(version, `Editado desde v${base.version}.`, false),
            error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo guardar los cambios.'),
          });
        });
    });
  }

  // Borrado real e irreversible de la politica entera (todas las versiones,
  // publicadas o no) -- pedido explicitamente por el operador (2026-08-18):
  // "si quiero la borro y hago una nueva". Ver hub/policy_store.py::delete_policy.
  deletePolicy(summary: PolicySummary): void {
    this.confirmService
      .confirm({
        title: 'Borrar politica',
        message: `Vas a borrar "${summary.policy_id}" por completo (${summary.version_count} version(es), incluida la activa si la tiene). Es irreversible -- no se puede deshacer con Rollback.`,
        confirmLabel: 'Borrar',
        requireReason: true,
        danger: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.policiesService.deletePolicy(summary.policy_id, reason).subscribe({
          next: () => {
            this.notifications.success('Politica eliminada.');
            if (this.selectedPolicyId() === summary.policy_id) {
              this.selectedPolicyId.set(null);
              this.versions.set([]);
            }
            this.policies.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo borrar la politica.'),
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
        this.doPublish(version, reason, false);
      });
  }

  // El backend rechaza el publish con 409 "significant_volume_change" si
  // simular la version contra la activa muestra un cambio de volumen
  // aceptado por encima del umbral (hub/api/routers/policies.py) -- en vez
  // de solo mostrar el error, se le pide al operador una segunda
  // confirmacion explicita con el detalle que mando el backend y, si
  // confirma, se reintenta el mismo publish con confirm_significant_change.
  private doPublish(version: PolicyVersion, reason: string, confirmSignificantChange: boolean): void {
    this.policiesService.publish(version.policy_id, version.version, reason, confirmSignificantChange).subscribe({
      next: () => {
        this.notifications.success('Version publicada.');
        this.refreshVersions(version.policy_id);
        this.policies.refresh();
      },
      error: (err) => {
        if (err?.error?.error_code === 'significant_volume_change') {
          this.confirmService
            .confirm({
              title: 'Cambio de volumen significativo',
              message: err.error.detail,
              confirmLabel: 'Publicar de todos modos',
              danger: true,
            })
            .subscribe(({ confirmed }) => {
              if (!confirmed) return;
              this.doPublish(version, reason, true);
            });
          return;
        }
        this.notifications.error(err?.error?.detail ?? 'No se pudo publicar.');
      },
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

  // Solo aplica a versiones en "draft": una version que alguna vez estuvo
  // publicada queda referenciada en el ledger y el backend rechaza borrarla
  // (409 not_a_draft) -- ver hub/policy_store.py::delete_draft_version.
  deleteDraft(version: PolicyVersion): void {
    this.confirmService
      .confirm({
        title: 'Borrar borrador',
        message: `Vas a borrar ${version.policy_id} v${version.version}. Nunca se publico, asi que no queda ningun registro de auditoria que dependa de esta version.`,
        confirmLabel: 'Borrar',
        requireReason: true,
        danger: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.policiesService.deleteDraft(version.policy_id, version.version, reason).subscribe({
          next: () => {
            this.notifications.success('Borrador eliminado.');
            this.refreshVersions(version.policy_id);
            this.policies.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo borrar el borrador.'),
        });
      });
  }
}
