import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import { OpenCTIConnectionTestResult, OpenCTISettings, OpenCTISettingsUpdate } from '../models';

// Wrapper 1:1 sobre hub/api/routers/opencti_settings.py.
@Injectable({ providedIn: 'root' })
export class OpenCTISettingsService {
  private readonly http = inject(HttpClient);
  private readonly base = `${environment.apiBaseUrl}/opencti-settings`;

  get(): Observable<OpenCTISettings> {
    return this.http.get<OpenCTISettings>(this.base);
  }

  update(payload: OpenCTISettingsUpdate): Observable<OpenCTISettings> {
    return this.http.put<OpenCTISettings>(this.base, payload);
  }

  test(): Observable<OpenCTIConnectionTestResult> {
    return this.http.post<OpenCTIConnectionTestResult>(`${this.base}/test`, {});
  }
}
