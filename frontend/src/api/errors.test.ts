import { describe, expect, it } from 'vitest';
import { parseRetryAfter } from './errors';

describe('parseRetryAfter', () => {
  const now = Date.parse('Wed, 21 Oct 2026 07:28:00 GMT');

  it('reads seconds', () => {
    expect(parseRetryAfter('6', now)).toBe(6);
    expect(parseRetryAfter(' 0 ', now)).toBe(0);
  });

  it('reads an HTTP date', () => {
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:28:10 GMT', now)).toBe(10);
    expect(parseRetryAfter('Wed, 21 Oct 2026 07:27:00 GMT', now)).toBe(0);
  });

  it('does not invent a time when the header is missing or invalid', () => {
    expect(parseRetryAfter(null, now)).toBeNull();
    expect(parseRetryAfter('soon', now)).toBeNull();
    expect(parseRetryAfter('-5', now)).toBeNull();
    expect(parseRetryAfter('', now)).toBeNull();
  });
});
