import { describe, expect, it } from 'vitest';
import { utf8Length, validateRegistration } from './validation';

const valid = { fullName: 'Ana Pérez', email: 'ana@example.com', password: 'secreto123', confirmPassword: 'secreto123' };

describe('validateRegistration', () => {
  it('accepts valid data', () => {
    expect(validateRegistration(valid)).toEqual({});
  });

  it('checks the name after trimming spaces (2-120)', () => {
    expect(validateRegistration({ ...valid, fullName: '  A  ' }).fullName).toBeDefined();
    expect(validateRegistration({ ...valid, fullName: '  Al  ' }).fullName).toBeUndefined();
    expect(validateRegistration({ ...valid, fullName: 'x'.repeat(121) }).fullName).toBeDefined();
  });

  it('checks the email format', () => {
    expect(validateRegistration({ ...valid, email: 'ana@' }).email).toBeDefined();
    expect(validateRegistration({ ...valid, email: 'ana example.com' }).email).toBeDefined();
  });

  it('requires 8 characters and at most 72 bytes; multibyte letters count double', () => {
    expect(validateRegistration({ ...valid, password: 'corta', confirmPassword: 'corta' }).password).toBeDefined();
    const at72 = 'ñ'.repeat(36);
    const over72 = 'ñ'.repeat(37);
    expect([utf8Length(at72), utf8Length(over72)]).toEqual([72, 74]);
    expect(validateRegistration({ ...valid, password: at72, confirmPassword: at72 }).password).toBeUndefined();
    expect(validateRegistration({ ...valid, password: over72, confirmPassword: over72 }).password).toBeDefined();
  });

  it('never trims the password and requires the same confirmation', () => {
    const spaced = '  secreto123  ';
    expect(validateRegistration({ ...valid, password: spaced, confirmPassword: 'secreto123' }).confirmPassword).toBeDefined();
    expect(validateRegistration({ ...valid, password: spaced, confirmPassword: spaced })).toEqual({});
  });
});
