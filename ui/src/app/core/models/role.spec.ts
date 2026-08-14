import { describe, expect, it } from 'vitest';

import { roleSatisfies } from './role';

describe('roleSatisfies', () => {
  it('allows a role to satisfy its own requirement', () => {
    expect(roleSatisfies('operator', 'operator')).toBe(true);
  });

  it('allows a higher role to satisfy a lower requirement', () => {
    expect(roleSatisfies('security-admin', 'viewer')).toBe(true);
  });

  it('rejects a lower role against a higher requirement', () => {
    expect(roleSatisfies('viewer', 'policy-admin')).toBe(false);
  });

  it('rejects when there is no role at all', () => {
    expect(roleSatisfies(null, 'viewer')).toBe(false);
  });
});
