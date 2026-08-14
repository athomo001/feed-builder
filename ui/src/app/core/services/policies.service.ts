import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { PolicyCreate, PolicySummary, PolicyVersion, SimulationReport } from '../models';

// Wrapper 1:1 sobre hub/api/routers/policies.py.
@Injectable({ providedIn: 'root' })
export class PoliciesService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/policies`;

  list(): Observable<PolicySummary[]> {
    return this.http.get<PolicySummary[]>(this.base);
  }

  versions(policyId: string): Observable<PolicyVersion[]> {
    return this.http.get<PolicyVersion[]>(`${this.base}/${encodeURIComponent(policyId)}/versions`);
  }

  create(payload: PolicyCreate): Observable<PolicyVersion> {
    return this.http.post<PolicyVersion>(this.base, payload);
  }

  simulate(policyId: string, sample?: unknown[], sampleSize = 50): Observable<SimulationReport> {
    return this.http.post<SimulationReport>(`${this.base}/${encodeURIComponent(policyId)}/simulate`, {
      sample: sample ?? null,
      sample_size: sampleSize,
    });
  }

  publish(policyId: string, version: number, reason: string): Observable<PolicyVersion> {
    return this.http.post<PolicyVersion>(`${this.base}/${encodeURIComponent(policyId)}/publish`, { version, reason });
  }

  rollback(policyId: string, version: number, reason: string): Observable<PolicyVersion> {
    return this.http.post<PolicyVersion>(`${this.base}/${encodeURIComponent(policyId)}/rollback`, { version, reason });
  }
}
