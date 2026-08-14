import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { IngestionControl, IngestionStatus } from '../models';

// Wrapper 1:1 sobre hub/api/routers/ingestion.py (spec/07 "OpenCTI / Ingesta").
@Injectable({ providedIn: 'root' })
export class IngestionService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/ingestion`;

  status(): Observable<IngestionStatus> {
    return this.http.get<IngestionStatus>(`${this.base}/status`);
  }

  pause(): Observable<IngestionControl> {
    return this.http.post<IngestionControl>(`${this.base}/pause`, {});
  }

  resume(): Observable<IngestionControl> {
    return this.http.post<IngestionControl>(`${this.base}/resume`, {});
  }

  reconcile(): Observable<IngestionControl> {
    return this.http.post<IngestionControl>(`${this.base}/reconcile`, {});
  }

  rewind(cursorValue: string, reason: string): Observable<IngestionControl> {
    return this.http.post<IngestionControl>(`${this.base}/rewind`, { cursor_value: cursorValue, reason });
  }
}
