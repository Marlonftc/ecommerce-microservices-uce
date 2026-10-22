import { describe, expect, it } from 'vitest';
import { formatDateTime, formatMoney, sameAmount } from './format';

describe('formatMoney', () => {
  it('formats decimal strings from the backend as dollars', () => {
    const text = formatMoney('899.99');
    expect(text).toContain('899,99');
    expect(text).toContain('$');
  });

  it('shows a dash for missing or invalid values', () => {
    expect(formatMoney(null)).toBe('—');
    expect(formatMoney('')).toBe('—');
    expect(formatMoney('abc')).toBe('—');
  });
});

describe('formatDateTime', () => {
  it('formats ISO dates and rejects invalid ones', () => {
    expect(formatDateTime('2026-10-22T15:00:00Z')).toMatch(/2026/);
    expect(formatDateTime('not a date')).toBe('—');
    expect(formatDateTime(null)).toBe('—');
  });
});

describe('sameAmount', () => {
  it('compares decimal strings without floating point', () => {
    expect(sameAmount('749.99', '749.99')).toBe(true);
    expect(sameAmount('749.9', '749.90')).toBe(true);
    expect(sameAmount('10', '10.00')).toBe(true);
    expect(sameAmount('0010.50', '10.5')).toBe(true);
    expect(sameAmount('749.99', '799.00')).toBe(false);
    expect(sameAmount('0.10', '0.1000000001')).toBe(false);
  });
});
