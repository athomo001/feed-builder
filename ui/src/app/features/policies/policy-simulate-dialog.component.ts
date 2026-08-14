import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTableModule } from '@angular/material/table';

import { PoliciesService } from '../../core/services';
import { SimulationReport } from '../../core/models';

export interface PolicySimulateDialogData {
  policyId: string;
}

// spec/07-ADMIN-UI-ANGULAR.md "Politicas", paso 3-4: "Simulador / Dry-run...
// Diff de resultados: cantidad de eventos filtrados vs. permitidos, con
// ejemplos reales". Sin sample: muestrea en vivo desde OpenCTI
// (hub/policy_simulation.py); nunca toca produccion.
@Component({
  selector: 'app-policy-simulate-dialog',
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressSpinnerModule,
    MatTableModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './policy-simulate-dialog.component.html',
  styleUrl: './policy-simulate-dialog.component.scss',
})
export class PolicySimulateDialogComponent {
  readonly data = inject<PolicySimulateDialogData>(MAT_DIALOG_DATA);
  private readonly policiesService = inject(PoliciesService);

  readonly sampleSize = signal(50);
  readonly running = signal(false);
  readonly error = signal<string | null>(null);
  readonly report = signal<SimulationReport | null>(null);
  readonly exampleColumns = ['stix_id', 'family', 'subtype', 'outcome', 'reason'];

  run(): void {
    this.running.set(true);
    this.error.set(null);
    this.policiesService.simulate(this.data.policyId, undefined, this.sampleSize()).subscribe({
      next: (report) => {
        this.report.set(report);
        this.running.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? 'No se pudo simular (revisa la conexion a OpenCTI).');
        this.running.set(false);
      },
    });
  }
}
