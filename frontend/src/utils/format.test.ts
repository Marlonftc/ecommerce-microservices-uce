import { describe, expect, it } from 'vitest';
import { formatDateTime, formatMoney } from './format';

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
