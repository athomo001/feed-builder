import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { Destination, DestinationCreate, DestinationTestResult, DestinationUpdate } from '../models';

// Wrapper 1:1 sobre hub/api/routers/destinations.py.
@Injectable({ providedIn: 'root' })
export class DestinationsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/destinations`;

  list(): Observable<Destination[]> {
    return this.http.get<Destination[]>(this.base);
  }

  get(id: string): Observable<Destination> {
    return this.http.get<Destination>(`${this.base}/${encodeURIComponent(id)}`);
  }

  create(payload: DestinationCreate): Observable<Destination> {
    return this.http.post<Destination>(this.base, payload);
  }

  update(id: string, payload: DestinationUpdate): Observable<Destination> {
    return this.http.put<Destination>(`${this.base}/${encodeURIComponent(id)}`, payload);
  }

  test(id: string, allowPrivateNetwork = false): Observable<DestinationTestResult> {
    return this.http.post<DestinationTestResult>(`${this.base}/${encodeURIComponent(id)}/test`, {
      allow_private_network: allowPrivateNetwork,
    });
  }

  pause(id: string): Observable<Destination> {
    return this.http.post<Destination>(`${this.base}/${encodeURIComponent(id)}/pause`, {});
  }

  resume(id: string): Observable<Destination> {
    return this.http.post<Destination>(`${this.base}/${encodeURIComponent(id)}/resume`, {});
  }
}
