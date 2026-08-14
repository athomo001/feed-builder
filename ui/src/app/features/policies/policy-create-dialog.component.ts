import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { Destination, PolicyCreate } from '../../core/models';

export interface PolicyCreateDialogData {
  destinations: Destination[];
}

// spec/07-ADMIN-UI-ANGULAR.md "Politicas", paso 1-2: "Formulario validado
// de reglas... Seleccion de destinos aplicables". allowed_iocs/ttl_days se
// editan como JSON crudo -- misma simplificacion deliberada que en Destinos.
@Component({
  selector: 'app-policy-create-dialog',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule, MatSelectModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './policy-create-dialog.component.html',
  styleUrl: './policy-create-dialog.component.scss',
})
export class PolicyCreateDialogComponent {
  readonly data = inject<PolicyCreateDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<PolicyCreateDialogComponent, PolicyCreate | null>);

  readonly policyId = signal('');
  readonly destinationId = signal(this.data.destinations[0]?.destination_id ?? '');
  readonly allowedIocsJson = signal('[{"family": "hash", "subtypes": ["sha256"]}]');
  readonly ttlDaysJson = signal('{"sha256": 30}');
  readonly jsonError = signal<string | null>(null);

  save(): void {
    let allowedIocs: PolicyCreate['allowed_iocs'];
    let ttlDays: Record<string, number>;
    try {
      allowedIocs = JSON.parse(this.allowedIocsJson());
      ttlDays = this.ttlDaysJson().trim() ? JSON.parse(this.ttlDaysJson()) : {};
    } catch {
      this.jsonError.set('allowed_iocs / ttl_days deben ser JSON valido.');
      return;
    }
    if (!this.policyId().trim() || !this.destinationId()) {
      this.jsonError.set('policy_id y destino son obligatorios.');
      return;
    }
    this.jsonError.set(null);
    this.dialogRef.close({
      policy_id: this.policyId().trim(),
      destination_id: this.destinationId(),
      allowed_iocs: allowedIocs,
      ttl_days: ttlDays,
    });
  }

  cancel(): void {
    this.dialogRef.close(null);
  }
}
