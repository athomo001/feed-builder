import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { AllowedIOC, Destination, PolicyCreate } from '../../core/models';
import { IocTypesPickerComponent } from '../../shared/ioc-types-picker/ioc-types-picker.component';

export interface PolicyCreateDialogData {
  destinations: Destination[];
  // Presente cuando el dialog se abre para "Editar" una politica existente
  // (ver policies.component.ts::editPolicy): precarga policy_id/destinos/
  // tipos/TTL desde la version activa (o la mas reciente) en vez de arrancar
  // en blanco. policy_id queda fijo (es la identidad de la politica); los
  // destinos SI se pueden cambiar aca -- una politica puede servir a varios
  // a la vez (modelo N:1, 2026-08-18).
  edit?: {
    policyId: string;
    destinationIds: string[];
    allowedIocs: AllowedIOC[];
    ttlDays: Record<string, number>;
    maxRecords: Record<string, number>;
  };
}

const DEFAULT_TTL_DAYS = 30;

// spec/07-ADMIN-UI-ANGULAR.md "Politicas", paso 1-2: "Formulario validado
// de reglas... Seleccion de destinos aplicables". allowed_iocs/ttl_days ya
// no se editan como JSON crudo (item #1 de ISSUES.md) -- se seleccionan con
// el mismo picker de family/subtype que Destinos, y el TTL se pide solo
// para los subtipos elegidos.
@Component({
  selector: 'app-policy-create-dialog',
  standalone: true,
  imports: [
    FormsModule,
    IocTypesPickerComponent,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './policy-create-dialog.component.html',
  styleUrl: './policy-create-dialog.component.scss',
})
export class PolicyCreateDialogComponent {
  readonly data = inject<PolicyCreateDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<PolicyCreateDialogComponent, PolicyCreate | null>);

  readonly isEdit = !!this.data.edit;
  readonly policyId = signal(this.data.edit?.policyId ?? '');
  // Multi-select: una politica puede aplicarse a varios destinos a la vez
  // (modelo N:1, 2026-08-18) -- pero un destino elegido aca deja de usar
  // cualquier OTRA politica que tuviera antes (lo aplica el backend,
  // hub/policy_store.py::assign_policy_to_destination).
  readonly destinationIds = signal<string[]>(this.data.edit?.destinationIds ?? []);
  readonly allowedTypes = signal<string[]>(
    this.data.edit
      ? this.data.edit.allowedIocs.flatMap((a) => a.subtypes.map((s) => `${a.family}/${s}`))
      : ['hash/sha256'],
  );
  readonly ttlDays = signal<Record<string, number>>(this.data.edit?.ttlDays ?? { sha256: DEFAULT_TTL_DAYS });
  // subtype -> cantidad maxima vigente (0 = sin tope propio, usa el default
  // parejo del destino). Combinable con TTL: el mas viejo se descarta
  // cuando se llena el cupo O cuando vence el TTL, lo que pase primero.
  readonly maxRecords = signal<Record<string, number>>(this.data.edit?.maxRecords ?? {});
  readonly formError = signal<string | null>(null);

  // Cambios masivos: tipear TTL/Cantidad subtipo por subtipo era una lata
  // con muchos tipos seleccionados (pedido explicito del operador,
  // 2026-08-18) -- estos dos valores se aplican a TODOS los subtipos
  // seleccionados de una, y siguen siendo editables individualmente despues.
  readonly bulkTtl = signal(DEFAULT_TTL_DAYS);
  readonly bulkMaxRecords = signal(0);

  readonly selectedSubtypes = computed(() => this.allowedTypes().map((v) => v.split('/')[1]));

  applyBulkTtl(): void {
    const value = this.bulkTtl();
    const updated: Record<string, number> = { ...this.ttlDays() };
    for (const subtype of this.selectedSubtypes()) updated[subtype] = value;
    this.ttlDays.set(updated);
  }

  applyBulkMaxRecords(): void {
    const value = this.bulkMaxRecords();
    const updated: Record<string, number> = { ...this.maxRecords() };
    for (const subtype of this.selectedSubtypes()) updated[subtype] = value;
    this.maxRecords.set(updated);
  }

  onTypesChange(next: string[]): void {
    this.allowedTypes.set(next);
    const currentTtl = this.ttlDays();
    const currentMax = this.maxRecords();
    const updatedTtl: Record<string, number> = {};
    const updatedMax: Record<string, number> = {};
    for (const v of next) {
      const subtype = v.split('/')[1];
      updatedTtl[subtype] = currentTtl[subtype] ?? DEFAULT_TTL_DAYS;
      updatedMax[subtype] = currentMax[subtype] ?? 0;
    }
    this.ttlDays.set(updatedTtl);
    this.maxRecords.set(updatedMax);
  }

  setTtl(subtype: string, days: number): void {
    this.ttlDays.set({ ...this.ttlDays(), [subtype]: days });
  }

  setMaxRecords(subtype: string, count: number): void {
    this.maxRecords.set({ ...this.maxRecords(), [subtype]: count });
  }

  save(): void {
    if (!this.policyId().trim() || this.destinationIds().length === 0) {
      this.formError.set('policy_id y al menos un destino son obligatorios.');
      return;
    }
    if (this.allowedTypes().length === 0) {
      this.formError.set('Elegi al menos un tipo de IOC.');
      return;
    }
    this.formError.set(null);

    const grouped = new Map<string, string[]>();
    for (const v of this.allowedTypes()) {
      const [family, subtype] = v.split('/');
      grouped.set(family, [...(grouped.get(family) ?? []), subtype]);
    }
    const allowedIocs: AllowedIOC[] = Array.from(grouped, ([family, subtypes]) => ({ family, subtypes }));

    // 0 = "sin tope propio" -- no se manda esa clave, para no pisar el
    // default del destino con "sin limite" a proposito (ver
    // hub/adapters/factory.py::build_adapter: subtype_max_records.get(subtype,
    // default_del_destino)).
    const maxRecords: Record<string, number> = {};
    for (const [subtype, count] of Object.entries(this.maxRecords())) {
      if (count > 0) maxRecords[subtype] = count;
    }

    this.dialogRef.close({
      policy_id: this.policyId().trim(),
      destination_ids: this.destinationIds(),
      allowed_iocs: allowedIocs,
      ttl_days: this.ttlDays(),
      max_records: maxRecords,
    });
  }

  cancel(): void {
    this.dialogRef.close(null);
  }
}
