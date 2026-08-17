import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTableModule } from '@angular/material/table';

import { AuthService } from '../../core/auth.service';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { NotificationService } from '../../core/notification.service';
import { AuditService, TokensService } from '../../core/services';
import { ALL_ROLES, ApiToken, AuditEntry, roleSatisfies } from '../../core/models';

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
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatTableModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './audit.component.html',
  styleUrl: './audit.component.scss',
})
export class AuditComponent {
  private readonly auditService = inject(AuditService);
  private readonly tokensService = inject(TokensService);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly columns = ['created_at', 'actor_role', 'action', 'resource_type', 'resource_id', 'result', 'reason'];

  readonly actorFilter = signal('');
  readonly actionFilter = signal('');
  readonly resourceTypeFilter = signal('');
  readonly entries = signal<AuditEntry[]>([]);
  readonly searched = signal(false);

  // "Gestion de tokens API" (spec/07 "Configuracion operativa" -- estaba
  // marcada explicitamente no disponible; pedida por el operador 2026-08-15).
  readonly tokenColumns = ['role', 'created_at', 'expires_at', 'revoked', 'actions'];
  readonly tokens = signal<ApiToken[]>([]);
  readonly allRoles = ALL_ROLES;
  readonly newTokenRole = signal<'viewer' | 'operator' | 'policy-admin' | 'security-admin'>('viewer');
  readonly newTokenExpiresInDays = signal<number | null>(null);
  readonly lastCreatedPlaintext = signal<string | null>(null);

  readonly canManageTokens = () => roleSatisfies(this.auth.role(), 'security-admin');

  constructor() {
    this.search();
    this.refreshTokens();
  }

  private refreshTokens(): void {
    // El GET exige security-admin server-side (spec/08): con otro rol este
    // llamado devuelve 403, esperado y silencioso -- no es un error a
    // mostrarle a un viewer que solo esta mirando el log de auditoria.
    this.tokensService.list().subscribe({
      next: (tokens) => this.tokens.set(tokens),
      error: () => this.tokens.set([]),
    });
  }

  createToken(): void {
    this.tokensService
      .create({ role: this.newTokenRole(), expires_in_days: this.newTokenExpiresInDays() || null })
      .subscribe({
        next: (created) => {
          this.lastCreatedPlaintext.set(created.plaintext);
          this.notifications.success(`Token creado (rol ${created.role}). Copialo ahora: no se vuelve a mostrar.`);
          this.refreshTokens();
        },
        error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo crear el token.'),
      });
  }

  revokeToken(token: ApiToken): void {
    this.confirmService
      .confirm({
        title: 'Revocar token',
        message: `El token de rol "${token.role}" (${token.token_id}) va a dejar de poder autenticarse de inmediato.`,
        confirmLabel: 'Revocar',
        danger: true,
      })
      .subscribe(({ confirmed }) => {
        if (!confirmed) return;
        this.tokensService.revoke(token.token_id).subscribe({
          next: () => {
            this.notifications.success('Token revocado.');
            this.refreshTokens();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo revocar el token.'),
        });
      });
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
