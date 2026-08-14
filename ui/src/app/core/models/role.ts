// Roles jerarquicos (spec/08-API-SECURITY.md "Roles locales"), mismo orden
// que hub/api/token_store.py _ROLE_RANK. Esto es solo UX: el servidor ya es
// quien realmente autoriza cada endpoint (spec/08 API5).
export type Role = 'viewer' | 'operator' | 'policy-admin' | 'security-admin';

const ROLE_RANK: Record<Role, number> = {
  viewer: 0,
  operator: 1,
  'policy-admin': 2,
  'security-admin': 3,
};

export function roleSatisfies(actual: Role | null, required: Role): boolean {
  if (!actual) return false;
  return ROLE_RANK[actual] >= ROLE_RANK[required];
}

export const ALL_ROLES: Role[] = ['viewer', 'operator', 'policy-admin', 'security-admin'];
