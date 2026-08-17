import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it } from 'vitest';

import { AuthService } from './auth.service';

describe('AuthService', () => {
  afterEach(() => {
    sessionStorage.clear();
  });

  it('starts unauthenticated', () => {
    const service = TestBed.inject(AuthService);
    expect(service.isAuthenticated()).toBe(false);
    expect(service.token()).toBeNull();
    expect(service.role()).toBeNull();
  });

  it('login sets token and role in memory', () => {
    const service = TestBed.inject(AuthService);
    service.login('secret-token', 'operator');
    expect(service.isAuthenticated()).toBe(true);
    expect(service.token()).toBe('secret-token');
    expect(service.role()).toBe('operator');
  });

  it('logout clears token and role', () => {
    const service = TestBed.inject(AuthService);
    service.login('secret-token', 'security-admin');
    service.logout();
    expect(service.isAuthenticated()).toBe(false);
    expect(service.token()).toBeNull();
    expect(service.role()).toBeNull();
  });

  it('login persists the token in sessionStorage so a refresh does not lose it', () => {
    const service = TestBed.inject(AuthService);
    service.login('secret-token', 'operator');
    expect(sessionStorage.getItem('hub_admin_token')).toBe('secret-token');
    expect(sessionStorage.getItem('hub_admin_role')).toBe('operator');
  });

  it('logout removes the persisted token from sessionStorage', () => {
    const service = TestBed.inject(AuthService);
    service.login('secret-token', 'operator');
    service.logout();
    expect(sessionStorage.getItem('hub_admin_token')).toBeNull();
    expect(sessionStorage.getItem('hub_admin_role')).toBeNull();
  });

  it('checkExistingSession restores a token persisted from a previous page load', async () => {
    sessionStorage.setItem('hub_admin_token', 'restored-token');
    sessionStorage.setItem('hub_admin_role', 'policy-admin');
    const service = TestBed.inject(AuthService);

    await service.checkExistingSession();

    expect(service.isAuthenticated()).toBe(true);
    expect(service.token()).toBe('restored-token');
    expect(service.role()).toBe('policy-admin');
  });
});
