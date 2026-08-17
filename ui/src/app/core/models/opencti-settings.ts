// Refleja hub/opencti_settings_store.py OpenCTISettings + hub/api/schemas.py
// OpenCTISettingsUpdate. El token nunca viaja en la respuesta del backend
// (solo `has_token`); en el payload de escritura es opcional y write-only
// (omitirlo conserva el token ya guardado).
export interface OpenCTISettings {
  url: string | null;
  tls_verify: boolean;
  ca_cert_path: string | null;
  stream_id: string | null;
  has_token: boolean;
}

export interface OpenCTISettingsUpdate {
  url: string;
  tls_verify?: boolean;
  ca_cert_path?: string | null;
  stream_id?: string | null;
  token?: string;
}

export interface OpenCTIConnectionTestResult {
  ok: boolean;
  error?: string;
}
