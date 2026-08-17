import { Role } from './role';

// Refleja hub/api/token_store.py APIToken + hub/api/routers/tokens.py
// (nunca expone token_hash). `plaintext` solo viene en la respuesta de
// creacion, una sola vez -- no se puede volver a pedir despues.
export interface ApiToken {
  token_id: string;
  role: Role;
  created_at: string;
  expires_at: string | null;
  revoked: boolean;
}

export interface ApiTokenCreated extends ApiToken {
  plaintext: string;
}

export interface ApiTokenCreate {
  role: Role;
  expires_in_days?: number | null;
}
