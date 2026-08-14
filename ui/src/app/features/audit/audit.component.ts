import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatTableModule } from '@angular/material/table';

import { AuditService } from '../../core/services';
import { AuditEntry } from '../../core/models';

const CSV_COLUMNS: (keyof AuditEntry)[] = [
  'created_at',
  'actor_token_id',
  'actor_role',
  'action',
  'resource_type',
  'resource_id',
  'result',
  'reason',
  'correlation_id',
];

// spec/07-ADMIN-UI-ANGULAR.md "Auditoria & Configuracion". Export CSV
// client-side de la pagina cargada (sin endpoint de export nuevo).
// "Configuracion operativa" queda marcada explicitamente no disponible.
@Component({
  selector: 'app-audit',
  standalone: true,
  imports: [DatePipe, FormsModule, MatButtonModule, MatFormFieldModule, MatInputModule, MatTableModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './audit.component.html',
  styleUrl: './audit.component.scss',
})
export class AuditComponent {
  private readonly auditService = inject(AuditService);

  readonly columns = ['created_at', 'actor_role', 'action', 'resource_type', 'resource_id', 'result', 'reason'];

  readonly actorFilter = signal('');
  readonly actionFilter = signal('');
  readonly resourceTypeFilter = signal('');
  readonly entries = signal<AuditEntry[]>([]);
  readonly searched = signal(false);

  constructor() {
    this.search();
  }

  search(): void {
    this.auditService
      .list({
        actor_token_id: this.actorFilter() || undefined,
        action: this.actionFilter() || undefined,
        resource_type: this.resourceTypeFilter() || undefined,
        limit: 100,
      })
      .subscribe((entries) => {
        this.entries.set(entries);
        this.searched.set(true);
      });
  }

  exportCsv(): void {
    const rows = this.entries();
    if (!rows.length) return;

    const lines = [CSV_COLUMNS.join(',')];
    for (const row of rows) {
      lines.push(CSV_COLUMNS.map((key) => this.csvEscape(row[key])).join(','));
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `audit-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  private csvEscape(value: unknown): string {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }
}
