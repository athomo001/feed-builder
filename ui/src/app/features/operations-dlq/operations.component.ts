import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RouterLink } from '@angular/router';
import { BehaviorSubject, catchError, interval, map, of, startWith, switchMap } from 'rxjs';

import { AuthService } from '../../core/auth.service';
import { ingestionHealthLabel, ingestionHealthState } from '../../core/ingestion-status';
import { NotificationService } from '../../core/notification.service';
import { pollingSignal } from '../../core/polling';
import { DeliveriesService, FeedsService, IngestionService } from '../../core/services';
import { FeedSummary, IngestionStatus, LedgerEntry, QueueDepth, deliveryId, roleSatisfies } from '../../core/models';
import { ConfirmService } from '../../shared/confirm-dialog/confirm.service';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';

const FEED_POLL_INTERVAL_MS = 15000;

// spec/07-ADMIN-UI-ANGULAR.md "Operaciones & DLQ": gestion de dead-letter
// (retry/discard con motivo obligatorio, nunca altera el evento original)
// + feeds materializados (preview, reconstruccion manual).
@Component({
  selector: 'app-operations',
  standalone: true,
  imports: [DecimalPipe, MatButtonModule, MatIconModule, MatTableModule, MatTooltipModule, RouterLink, StatusBadgeComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './operations.component.html',
  styleUrl: './operations.component.scss',
})
export class OperationsComponent {
  private readonly deliveriesService = inject(DeliveriesService);
  private readonly feedsService = inject(FeedsService);
  private readonly ingestionService = inject(IngestionService);
  private readonly confirmService = inject(ConfirmService);
  private readonly notifications = inject(NotificationService);
  private readonly auth = inject(AuthService);

  readonly dlqColumns = ['event_id', 'stix_id', 'destination_id', 'state', 'attempts', 'error', 'actions'];
  // Agrupado por destino (ver feedsByDestination): dentro de cada grupo el
  // destino ya es el titulo, asi que la fila no lo repite.
  readonly feedColumns = ['subtype', 'entries', 'link', 'actions'];

  readonly deadLetters = pollingSignal<LedgerEntry[]>(() => this.deliveriesService.deadLetters(), 10000, []);
  readonly feeds = pollingSignal<FeedSummary[]>(() => this.feedsService.list(), FEED_POLL_INTERVAL_MS, []);

  // Con varios destinos configurados (Wazuh, MikroTik, TXT...) una tabla
  // plana mezclando todos los subtipos de todos los destinos se volvia
  // dificil de revisar uno por uno -- pedido explicito del operador
  // (2026-08-18): "que va soltando por destino, asi es mas facil de ver".
  readonly feedsByDestination = computed(() => {
    const groups = new Map<string, FeedSummary[]>();
    for (const f of this.feeds()) {
      const list = groups.get(f.destination_id) ?? [];
      list.push(f);
      groups.set(f.destination_id, list);
    }
    return Array.from(groups.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([destinationId, feeds]) => ({ destinationId, feeds }));
  });

  // Destinos api_push con rate_limit_per_minute: lo que esta esperando
  // turno (PENDING en el ledger) porque el destino esta al tope de su tasa
  // -- visible aca para que no se lea como "se perdio" (spec/04 "el worker
  // respeta el limite y encola el excedente; nunca lo descarta").
  readonly queueDepth = pollingSignal<QueueDepth[]>(() => this.deliveriesService.queue(), 10000, []);

  // Los "Entradas" de un feed solo suben si el Live Stream esta metiendo
  // eventos nuevos -- mostrar este estado al lado de la tabla evita que un
  // conteo estancado se lea como "esto esta roto" cuando en realidad es
  // "no hay ingesta activa ahora mismo" (item #4 de ISSUES.md).
  readonly ingestionStatus = pollingSignal<IngestionStatus | null>(() => this.ingestionService.status(), FEED_POLL_INTERVAL_MS, null);

  readonly ingestionState = () => ingestionHealthState(this.ingestionStatus());
  readonly ingestionStateLabel = () => ingestionHealthLabel(this.ingestionState());

  // El feed a previsualizar se elige con un click ("Ver preview"), pero una
  // vez elegido se sigue re-consultando sola mientras quede seleccionado
  // (mismo intervalo que la lista de feeds) -- si no, el preview quedaba
  // congelado en el momento del click aunque el feed siguiera creciendo.
  private readonly previewTrigger$ = new BehaviorSubject<string | null>(null);
  readonly previewFeedId = toSignal(this.previewTrigger$, { initialValue: null });
  readonly previewLines = toSignal(
    this.previewTrigger$.pipe(
      switchMap((feedId) =>
        feedId === null
          ? of<string[]>([])
          : interval(FEED_POLL_INTERVAL_MS).pipe(
              startWith(0),
              switchMap(() => this.feedsService.preview(feedId).pipe(map((p) => p.preview), catchError(() => of<string[]>([])))),
            ),
      ),
    ),
    { initialValue: [] as string[] },
  );

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
    this.previewTrigger$.next(feed.feed_id);
  }

  // nginx sirve /feeds/ en el mismo origen que la UI (ver nginx.conf), asi
  // que el link completo es simplemente location.origin + public_path --
  // sin necesidad de que el backend conozca su propio hostname publico.
  feedUrl(feed: FeedSummary): string {
    return `${location.origin}${feed.public_path}`;
  }

  copyFeedUrl(feed: FeedSummary): void {
    navigator.clipboard
      .writeText(this.feedUrl(feed))
      .then(() => this.notifications.success('Link copiado.'))
      .catch(() => this.notifications.error('No se pudo copiar el link.'));
  }

  copyAllFeedUrls(): void {
    const urls = this.feeds().map((f) => this.feedUrl(f));
    if (!urls.length) return;
    navigator.clipboard
      .writeText(urls.join('\n'))
      .then(() => this.notifications.success(`${urls.length} links copiados.`))
      .catch(() => this.notifications.error('No se pudieron copiar los links.'));
  }

  // Export CSV client-side de la pagina cargada (mismo patron que
  // audit.component.ts), sin endpoint de export nuevo.
  exportFeedsCsv(): void {
    const rows = this.feeds();
    if (!rows.length) return;

    const header = ['feed_id', 'destination_id', 'subtype', 'entries', 'link'];
    const lines = [header.join(',')];
    for (const f of rows) {
      lines.push(
        [f.feed_id, f.destination_id, f.subtype, f.entries, this.feedUrl(f)].map((v) => this.csvEscape(v)).join(','),
      );
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `feeds-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  private csvEscape(value: unknown): string {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
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
