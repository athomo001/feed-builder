import { DatePipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatTableModule } from '@angular/material/table';

import { EventsService } from '../../core/services';
import { LedgerEntry } from '../../core/models';
import { StatusBadgeComponent } from '../../shared/status-badge/status-badge.component';

// spec/07-ADMIN-UI-ANGULAR.md "Observabilidad & Logs": Inspector del Event
// Ledger real. El "Visor de logs en vivo" queda marcado explicitamente no
// disponible -- no hay log store todavia (spec/PROJECT-MAP.md), en vez de
// simular logs falsos.
@Component({
  selector: 'app-observability',
  standalone: true,
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
    MatTableModule,
    StatusBadgeComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './observability.component.html',
  styleUrl: './observability.component.scss',
})
export class ObservabilityComponent {
  private readonly eventsService = inject(EventsService);

  readonly columns = ['event_id', 'stix_id', 'destination_id', 'state', 'reason', 'updated_at', 'actions'];
  readonly timelineColumns = ['destination_id', 'policy_version', 'state', 'reason', 'attempts', 'updated_at'];

  readonly eventIdFilter = signal('');
  readonly stixIdFilter = signal('');
  readonly destinationIdFilter = signal('');
  readonly stateFilter = signal('');

  readonly results = signal<LedgerEntry[]>([]);
  readonly searched = signal(false);
  readonly selectedEventId = signal<string | null>(null);
  readonly timeline = signal<LedgerEntry[]>([]);

  search(): void {
    this.eventsService
      .search({
        event_id: this.eventIdFilter() || undefined,
        stix_id: this.stixIdFilter() || undefined,
        destination_id: this.destinationIdFilter() || undefined,
        state: this.stateFilter() || undefined,
        limit: 50,
      })
      .subscribe((entries) => {
        this.results.set(entries);
        this.searched.set(true);
      });
  }

  viewTimeline(eventId: string): void {
    this.selectedEventId.set(eventId);
    this.eventsService.timeline(eventId).subscribe((entries) => this.timeline.set(entries));
  }
}
