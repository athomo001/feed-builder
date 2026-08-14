import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';

import { AuthService } from '../../core/auth.service';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { DeliveriesService, FeedsService } from '../../core/services';
import { FeedSummary, LedgerEntry, deliveryId, roleSatisfies } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';

// spec/07-ADMIN-UI-ANGULAR.md "Operaciones & DLQ": gestion de dead-letter
// (retry/discard con motivo obligatorio, nunca altera el evento original)
// + feeds materializados (preview, reconstruccion manual).
@Component({
  selector: 'app-operations',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatTableModule, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './operations.component.html',
  styleUrl: './operations.component.scss',
})
export class OperationsComponent {
  private readonly deliveriesService = inject(DeliveriesService);
  private readonly feedsService = inject(FeedsService);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly dlqColumns = ['event_id', 'stix_id', 'destination_id', 'state', 'attempts', 'error', 'actions'];
  readonly feedColumns = ['feed_id', 'destination_id', 'subtype', 'entries', 'actions'];

  readonly deadLetters = pollingSignal<LedgerEntry[]>(() => this.deliveriesService.deadLetters(), 10000, []);
  readonly feeds = pollingSignal<FeedSummary[]>(() => this.feedsService.list(), 15000, []);
  readonly previewFeedId = signal<string | null>(null);
  readonly previewLines = signal<string[]>([]);

  readonly canOperate = () => roleSatisfies(this.auth.role(), 'operator');

  retry(entry: LedgerEntry): void {
    this.deliveriesService.retry(deliveryId(entry)).subscribe({
      next: (updated) => {
        this.notifications.success(`Reintento hecho: ahora "${updated.state}".`);
        this.deadLetters.refresh();
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo reintentar.'),
    });
  }

  discard(entry: LedgerEntry): void {
    this.confirmService
      .confirm({
        title: 'Descartar entrega',
        message: `Vas a descartar la entrega de "${entry.stix_id}" hacia "${entry.destination_id}". El evento original en el ledger no se altera.`,
        confirmLabel: 'Descartar',
        requireReason: true,
        danger: true,
      })
      .subscribe(({ confirmed, reason }) => {
        if (!confirmed || !reason) return;
        this.deliveriesService.discard(deliveryId(entry), reason).subscribe({
          next: () => {
            this.notifications.success('Entrega descartada.');
            this.deadLetters.refresh();
          },
          error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo descartar.'),
        });
      });
  }

  preview(feed: FeedSummary): void {
    this.feedsService.preview(feed.feed_id).subscribe((p) => {
      this.previewFeedId.set(feed.feed_id);
      this.previewLines.set(p.preview);
    });
  }

  rebuild(feed: FeedSummary): void {
    this.feedsService.rebuild(feed.feed_id).subscribe({
      next: (r) => {
        this.notifications.success(`Feed reconstruido: ${r.written} escritos, ${r.skipped_capacity} fuera de cupo.`);
        this.feeds.refresh();
      },
      error: (err) => this.notifications.error(err?.error?.detail ?? 'No se pudo reconstruir el feed.'),
    });
  }
}
