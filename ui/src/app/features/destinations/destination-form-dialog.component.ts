import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { AdapterType, Destination, DestinationCreate } from '../../core/models';

export interface DestinationFormDialogData {
  mode: 'create' | 'edit';
  destination?: Destination;
}

// spec/07-ADMIN-UI-ANGULAR.md "Destinos": "Crear un destino requiere menos
// de cinco pasos". capacity/format_options se editan como JSON crudo --
// simplificacion deliberada en vez de un formulario dinamico por adapter.
@Component({
  selector: 'app-destination-form-dialog',
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './destination-form-dialog.component.html',
  styleUrl: './destination-form-dialog.component.scss',
})
export class DestinationFormDialogComponent {
  readonly data = inject<DestinationFormDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<DestinationFormDialogComponent, DestinationCreate | null>);

  private readonly d = this.data.destination;

  readonly isEdit = this.data.mode === 'edit';
  readonly destinationId = signal(this.d?.destination_id ?? '');
  readonly name = signal(this.d?.name ?? '');
  readonly adapter = signal<AdapterType>(this.d?.adapter ?? 'txt_feed');
  readonly endpoint = signal(this.d?.endpoint ?? '');
  readonly credentialRef = signal(this.d?.credential_ref ?? '');
  readonly format = signal(this.d?.format ?? 'txt');
  readonly allowedIocTypes = signal((this.d?.allowed_ioc_types ?? []).join(', '));
  readonly timeoutSeconds = signal(this.d?.timeout_seconds ?? 15);
  readonly supportsDelete = signal(this.d?.supports_delete ?? false);
  readonly capacityJson = signal(JSON.stringify(this.d?.capacity ?? {}, null, 2));
  readonly formatOptionsJson = signal(JSON.stringify(this.d?.format_options ?? {}, null, 2));
  readonly jsonError = signal<string | null>(null);

  save(): void {
    let capacity: Record<string, unknown>;
    let formatOptions: Record<string, unknown>;
    try {
      capacity = this.capacityJson().trim() ? JSON.parse(this.capacityJson()) : {};
    } catch {
      this.jsonError.set('El campo "capacity" no es JSON valido.');
      return;
    }
    try {
      formatOptions = this.formatOptionsJson().trim() ? JSON.parse(this.formatOptionsJson()) : {};
    } catch {
      this.jsonError.set('El campo "format_options" no es JSON valido.');
      return;
    }
    this.jsonError.set(null);

    const payload: DestinationCreate = {
      destination_id: this.destinationId().trim(),
      name: this.name().trim(),
      adapter: this.adapter(),
      endpoint: this.endpoint().trim() || null,
      credential_ref: this.credentialRef().trim() || null,
      format: this.format().trim() || 'txt',
      allowed_ioc_types: this.allowedIocTypes()
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
      timeout_seconds: this.timeoutSeconds(),
      supports_delete: this.supportsDelete(),
      capacity,
      format_options: formatOptions,
    };
    this.dialogRef.close(payload);
  }

  cancel(): void {
    this.dialogRef.close(null);
  }
}
