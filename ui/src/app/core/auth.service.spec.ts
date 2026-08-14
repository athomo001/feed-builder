import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { AuthService } from './auth.service';

describe('AuthService', () => {
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
});
