import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { ApiToken, ApiTokenCreate, ApiTokenCreated } from '../models';

// Wrapper 1:1 sobre hub/api/routers/tokens.py.
@Injectable({ providedIn: 'root' })
export class TokensService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/tokens`;

  list(): Observable<ApiToken[]> {
    return this.http.get<ApiToken[]>(this.base);
  }

  create(payload: ApiTokenCreate): Observable<ApiTokenCreated> {
    return this.http.post<ApiTokenCreated>(this.base, payload);
  }

  revoke(tokenId: string): Observable<{ token_id: string; revoked: boolean }> {
    return this.http.post<{ token_id: string; revoked: boolean }>(`${this.base}/${encodeURIComponent(tokenId)}/revoke`, {});
  }
}
