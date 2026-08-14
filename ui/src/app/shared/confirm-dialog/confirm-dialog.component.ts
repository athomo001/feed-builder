import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

// spec/07-ADMIN-UI-ANGULAR.md "Ninguna accion destructiva o irreversible se
// ejecuta con un clic accidental: exige modal de confirmacion explicita".
export interface ConfirmDialogData {
  title: string;
  message: string;
  confirmLabel?: string;
  requireReason?: boolean;
  danger?: boolean;
}

export interface ConfirmDialogResult {
  confirmed: boolean;
  reason?: string;
}

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [FormsModule, MatDialogModule, MatButtonModule, MatFormFieldModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>
      <p>{{ data.message }}</p>
      @if (data.requireReason) {
        <mat-form-field appearance="outline" style="width: 100%">
          <mat-label>Motivo (obligatorio)</mat-label>
          <textarea matInput [(ngModel)]="reason" rows="2" required></textarea>
        </mat-form-field>
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button (click)="cancel()">Cancelar</button>
      <button
        mat-flat-button
        [color]="data.danger ? 'warn' : 'primary'"
        [disabled]="!!data.requireReason && !reason.trim()"
        (click)="confirm()"
      >
        {{ data.confirmLabel || 'Confirmar' }}
      </button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialogComponent {
  readonly data = inject<ConfirmDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<ConfirmDialogComponent, ConfirmDialogResult>);
  reason = '';

  confirm(): void {
    this.dialogRef.close({ confirmed: true, reason: this.reason.trim() || undefined });
  }

  cancel(): void {
    this.dialogRef.close({ confirmed: false });
  }
}
