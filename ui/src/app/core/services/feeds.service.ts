import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { FeedPreview, FeedRebuildResult, FeedSummary } from '../models';

// Wrapper 1:1 sobre hub/api/routers/feeds.py. `feedId` es
// `destination_id::subtype`.
@Injectable({ providedIn: 'root' })
export class FeedsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/feeds`;

  list(): Observable<FeedSummary[]> {
    return this.http.get<FeedSummary[]>(this.base);
  }

  preview(feedId: string): Observable<FeedPreview> {
    return this.http.get<FeedPreview>(`${this.base}/${encodeURIComponent(feedId)}/preview`);
  }

  rebuild(feedId: string): Observable<FeedRebuildResult> {
    return this.http.post<FeedRebuildResult>(`${this.base}/${encodeURIComponent(feedId)}/rebuild`, {});
  }
}
