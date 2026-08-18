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

  // Reasigna que destinos usan esta politica sin crear una version nueva
  // (modelo N:1, 2026-08-18) -- reemplaza el set completo: agrega lo que
  // falte, saca lo que ya no este en la lista.
  updateAssignments(policyId: string, destinationIds: string[]): Observable<{ policy_id: string; destination_ids: string[] }> {
    return this.http.put<{ policy_id: string; destination_ids: string[] }>(
      `${this.base}/${encodeURIComponent(policyId)}/assignments`,
      { destination_ids: destinationIds },
    );
  }

  simulate(policyId: string, sample?: unknown[], sampleSize = 50): Observable<SimulationReport> {
    return this.http.post<SimulationReport>(`${this.base}/${encodeURIComponent(policyId)}/simulate`, {
      sample: sample ?? null,
      sample_size: sampleSize,
    });
  }

  publish(policyId: string, version: number, reason: string, confirmSignificantChange = false): Observable<PolicyVersion> {
    return this.http.post<PolicyVersion>(`${this.base}/${encodeURIComponent(policyId)}/publish`, {
      version,
      reason,
      confirm_significant_change: confirmSignificantChange,
    });
  }

  rollback(policyId: string, version: number, reason: string): Observable<PolicyVersion> {
    return this.http.post<PolicyVersion>(`${this.base}/${encodeURIComponent(policyId)}/rollback`, { version, reason });
  }

  // Solo borra un draft que nunca se publico (409 si no) -- ver
  // hub/policy_store.py::delete_draft_version.
  deleteDraft(policyId: string, version: number, reason: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${encodeURIComponent(policyId)}/versions/${version}`, { body: { reason } });
  }

  // Borrado real de TODAS las versiones (publicadas o no) -- ver
  // hub/policy_store.py::delete_policy.
  deletePolicy(policyId: string, reason: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/${encodeURIComponent(policyId)}`, { body: { reason } });
  }
}
