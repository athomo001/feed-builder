import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { AuditEntry, AuditFilters } from '../models';

// Wrapper 1:1 sobre hub/api/routers/audit.py.
@Injectable({ providedIn: 'root' })
export class AuditService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/audit`;

  list(filters: AuditFilters = {}): Observable<AuditEntry[]> {
    const params: Record<string, string> = {};
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== null && value !== '') {
        params[key] = String(value);
      }
    }
    return this.http.get<AuditEntry[]>(this.base, { params });
  }
}
