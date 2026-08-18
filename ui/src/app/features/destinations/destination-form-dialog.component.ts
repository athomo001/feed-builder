import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';

import { AdapterType, Destination, DestinationCreate } from '../../core/models';

export interface DestinationFormDialogData {
  mode: 'create' | 'edit';
  destination?: Destination;
}

// `format` no se edita a mano: cada adapter file_feed exige un valor fijo
// (validate() de cada adaptador, ver hub/adapters/*.py) y http_push/
// qradar_reference_set no lo usan para nada -- exponerlo como texto libre
// solo invitaba a un valor invalido sin ganar flexibilidad real.
const ADAPTER_FIXED_FORMAT: Record<AdapterType, string> = {
  txt_feed: 'txt',
  http_push: 'txt',
  csv_feed: 'csv',
  mikrotik_rsc: 'rsc',
  wazuh_cdb: 'cdb',
  stix_bundle_feed: 'stix2.1',
  qradar_reference_set: 'txt',
  taxii2: 'stix2.1',
};

// Adapters que autentican/direccionan por red (ver hub/adapters/*.py:
// http_push_adapter.py, qradar_adapter.py) -- los unicos que leen endpoint/
// credential_ref/supports_delete de verdad.
const NETWORK_ADAPTERS = new Set<AdapterType>(['http_push', 'qradar_reference_set']);

// Adapters que escriben a un archivo materializado (ver hub/txt_feed.py,
// hub/stix_bundle.py) -- los unicos que leen capacity.max_records_per_file/
// overflow_strategy.
const FILE_ADAPTERS = new Set<AdapterType>(['txt_feed', 'csv_feed', 'mikrotik_rsc', 'wazuh_cdb', 'stix_bundle_feed']);

const CSV_DEFAULT_COLUMNS = ['family', 'subtype', 'value', 'score', 'confidence', 'marking', 'created_at', 'valid_until'];

// spec/07-ADMIN-UI-ANGULAR.md "Destinos": "Crear un destino requiere menos
// de cinco pasos". UI progresiva -- solo se muestran los campos que el
// adapter elegido realmente usa (ver hub/adapters/*.py), en vez de todos
// los campos tecnicos crudos a la vez (item #3 de ISSUES.md).
@Component({
  selector: 'app-destination-form-dialog',
  standalone: true,
  imports: [
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './destination-form-dialog.component.html',
  styleUrl: './destination-form-dialog.component.scss',
})
export class DestinationFormDialogComponent {
  readonly data = inject<DestinationFormDialogData>(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<DestinationFormDialogComponent, DestinationCreate | null>);

  private readonly d = this.data.destination;
  private readonly fo = this.d?.format_options ?? {};
  private readonly cap = this.d?.capacity ?? {};

  readonly isEdit = this.data.mode === 'edit';
  readonly destinationId = signal(this.d?.destination_id ?? '');
  readonly name = signal(this.d?.name ?? '');
  readonly adapter = signal<AdapterType>(this.d?.adapter ?? 'txt_feed');
  readonly endpoint = signal(this.d?.endpoint ?? '');
  readonly credentialRef = signal(this.d?.credential_ref ?? '');
  // capacity (solo adapters api_push) -- tasa maxima Y tamano de lote por
  // vuelta de drenado, dos diales distintos (spec/04 "Capacidad y
  // throughput por destino"). El excedente se encola, nunca se descarta
  // (hub/delivery_queue_store.py).
  readonly rateLimitPerMinute = signal(Number(this.cap['rate_limit_per_minute'] ?? 0));
  readonly batchSize = signal(Number(this.cap['batch_size'] ?? 0));
  readonly timeoutSeconds = signal(this.d?.timeout_seconds ?? 15);
  readonly supportsDelete = signal(this.d?.supports_delete ?? false);
  readonly jsonError = signal<string | null>(null);

  // capacity (solo adapters file_feed) -- limite por cantidad Y/O por peso,
  // lo que se cumpla primero (spec/04 "Capacidad y throughput por destino").
  readonly maxRecordsPerFile = signal(Number(this.cap['max_records_per_file'] ?? 0));
  readonly maxFileSizeBytes = signal(Number(this.cap['max_file_size_bytes'] ?? 0));
  readonly overflowStrategy = signal(String(this.cap['overflow_strategy'] ?? 'newest_first'));

  // format_options por adapter (solo se leen/envian los que aplican)
  readonly csvColumns = signal(((this.fo['columns'] as string[] | undefined) ?? CSV_DEFAULT_COLUMNS).join(', '));
  readonly csvDelimiter = signal(String(this.fo['delimiter'] ?? ','));
  readonly csvIncludeHeader = signal(this.fo['include_header'] !== false);
  readonly csvQuoting = signal(String(this.fo['quoting'] ?? 'minimal'));
  readonly mikrotikListName = signal(String(this.fo['list_name'] ?? this.d?.destination_id ?? ''));
  readonly wazuhIncludeTag = signal(this.fo['include_tag'] === true);
  readonly qradarReferenceSetName = signal(String(this.fo['reference_set_name'] ?? ''));

  readonly usesNetwork = computed(() => NETWORK_ADAPTERS.has(this.adapter()));
  readonly usesFile = computed(() => FILE_ADAPTERS.has(this.adapter()));
  readonly showsCsvOptions = computed(() => this.adapter() === 'csv_feed');
  readonly showsMikrotikOptions = computed(() => this.adapter() === 'mikrotik_rsc');
  readonly showsWazuhOptions = computed(() => this.adapter() === 'wazuh_cdb');
  readonly showsQradarOptions = computed(() => this.adapter() === 'qradar_reference_set');

  save(): void {
    if (!this.destinationId().trim() || !this.name().trim()) {
      this.jsonError.set('ID y nombre son obligatorios.');
      return;
    }
    if (this.usesNetwork() && !this.endpoint().trim()) {
      this.jsonError.set('endpoint es obligatorio para este adapter.');
      return;
    }
    if (this.showsQradarOptions() && !this.qradarReferenceSetName().trim()) {
      this.jsonError.set('reference_set_name es obligatorio para qradar_reference_set.');
      return;
    }
    this.jsonError.set(null);

    const formatOptions: Record<string, unknown> = {};
    if (this.showsCsvOptions()) {
      formatOptions['columns'] = this.csvColumns()
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      formatOptions['delimiter'] = this.csvDelimiter();
      formatOptions['include_header'] = this.csvIncludeHeader();
      formatOptions['quoting'] = this.csvQuoting();
    } else if (this.showsMikrotikOptions()) {
      formatOptions['list_name'] = this.mikrotikListName().trim() || this.destinationId().trim();
    } else if (this.showsWazuhOptions()) {
      formatOptions['include_tag'] = this.wazuhIncludeTag();
    } else if (this.showsQradarOptions()) {
      formatOptions['reference_set_name'] = this.qradarReferenceSetName().trim();
    }

    const capacity: Record<string, unknown> = this.usesFile()
      ? {
          max_records_per_file: this.maxRecordsPerFile(),
          max_file_size_bytes: this.maxFileSizeBytes(),
          overflow_strategy: this.overflowStrategy(),
        }
      : this.usesNetwork()
        ? {
            rate_limit_per_minute: this.rateLimitPerMinute(),
            batch_size: this.batchSize(),
          }
        : {};

    const payload: DestinationCreate = {
      destination_id: this.destinationId().trim(),
      name: this.name().trim(),
      adapter: this.adapter(),
      endpoint: this.usesNetwork() ? this.endpoint().trim() : null,
      credential_ref: this.usesNetwork() ? this.credentialRef().trim() || null : null,
      format: ADAPTER_FIXED_FORMAT[this.adapter()],
      timeout_seconds: this.timeoutSeconds(),
      supports_delete: this.usesNetwork() ? this.supportsDelete() : false,
      capacity,
      format_options: formatOptions,
    };
    this.dialogRef.close(payload);
  }

  cancel(): void {
    this.dialogRef.close(null);
  }
}
