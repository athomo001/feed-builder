import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LedgerEntry } from '../models';

export interface EventSearchFilters {
  event_id?: string;
  stix_id?: string;
  destination_id?: string;
  state?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

// Wrapper 1:1 sobre hub/api/routers/events.py (Inspector del Event Ledger,
// spec/07-ADMIN-UI-ANGULAR.md -- acotado a lo que el ledger ya guarda).
@Injectable({ providedIn: 'root' })
export class EventsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/events`;

  search(filters: EventSearchFilters): Observable<LedgerEntry[]> {
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== null && value !== '') {
        params[key] = String(value);
      }
    }
    return this.http.get<LedgerEntry[]>(this.base, { params });
  }

  timeline(eventId: string): Observable<LedgerEntry[]> {
    return this.http.get<LedgerEntry[]>(`${this.base}/${encodeURIComponent(eventId)}`);
  }
}
