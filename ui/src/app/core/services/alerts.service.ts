import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { Alert } from '../models';

// Wrapper 1:1 sobre hub/api/routers/alerts.py.
@Injectable({ providedIn: 'root' })
export class AlertsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/alerts`;

  list(params?: { severity?: string; state?: string; component?: string }): Observable<Alert[]> {
    return this.http.get<Alert[]>(this.base, { params: params as Record<string, string> });
  }

  acknowledge(alertId: string): Observable<Alert> {
    return this.http.post<Alert>(`${this.base}/${encodeURIComponent(alertId)}/acknowledge`, {});
  }
}
