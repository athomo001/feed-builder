import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { LedgerEntry } from '../models';

// Wrapper 1:1 sobre hub/api/routers/deliveries.py. `id` es el delivery_id
// codificado como `event_id::destination_id::policy_version`
// (ver hub/ledger.ts deliveryId()).
@Injectable({ providedIn: 'root' })
export class DeliveriesService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/deliveries`;

  deadLetters(): Observable<LedgerEntry[]> {
    return this.http.get<LedgerEntry[]>(`${this.base}/dead-letters`);
  }

  retry(id: string): Observable<LedgerEntry> {
    return this.http.post<LedgerEntry>(`${this.base}/${encodeURIComponent(id)}/retry`, {});
  }

  discard(id: string, reason: string): Observable<LedgerEntry> {
    return this.http.post<LedgerEntry>(`${this.base}/${encodeURIComponent(id)}/discard`, { reason });
  }
}
