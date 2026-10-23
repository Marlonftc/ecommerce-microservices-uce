import { describe, expect, it } from 'vitest';
import { availabilityOf, canAddToCart, knownUnits } from './availability';
import { categoryLabel } from './categories';
import { pageOffset, parseProductId, readCatalogParams, toSearchParams } from './params';

describe('catalogue parameters in the URL', () => {
  it('reads valid values', () => {
    expect(readCatalogParams(new URLSearchParams('q=laptop&categoria=audio&pagina=3'))).toEqual({
      q: 'laptop', category: 'audio', page: 3,
    });
  });

  it.each(['abc', '0', '-2', '1.5', '', '99999999'])('an invalid page (%j) becomes page 1', (pagina) => {
    expect(readCatalogParams(new URLSearchParams({ pagina })).page).toBe(1);
  });

  it('limits the search to 100 characters, like product-service', () => {
    expect(readCatalogParams(new URLSearchParams({ q: 'x'.repeat(150) })).q).toHaveLength(100);
  });

  it('writes only what differs from the defaults', () => {
    expect(toSearchParams({ q: ' mouse ', category: '', page: 1 }).toString()).toBe('q=mouse');
    expect(toSearchParams({ q: '', category: 'audio', page: 2 }).toString()).toBe('categoria=audio&pagina=2');
    expect(pageOffset(1)).toBe(0);
    expect(pageOffset(3)).toBe(24);
  });

  it('product ids: positive 32-bit integers only', () => {
    expect(parseProductId('7')).toBe(7);
    expect(parseProductId('2147483647')).toBe(2147483647);
    for (const raw of ['abc', '0', '-1', '1.5', '07', '2147483648', '1e3', undefined]) {
      expect(parseProductId(raw)).toBeNull();
    }
  });
});

describe('category labels', () => {
  it('turns the stored slug into a readable label', () => {
    expect(categoryLabel('laptops')).toBe('Laptops');
    expect(categoryLabel('ropa-deportiva')).toBe('Ropa deportiva');
    expect(categoryLabel('electrónica')).toBe('Electrónica');
  });
});

describe('availability', () => {
  const ready = { status: 'ready' as const, byId: new Map([[1, 5], [2, 0]]) };

  it('distinguishes in stock, sold out, no stock record and unknown', () => {
    expect(availabilityOf(1, ready)).toEqual({ state: 'in-stock', available: 5 });
    expect(availabilityOf(2, ready)).toEqual({ state: 'sold-out' });
    expect(availabilityOf(3, ready)).toEqual({ state: 'no-record' });
    expect(availabilityOf(1, { status: 'error' })).toEqual({ state: 'unknown' });
    expect(availabilityOf(1, { status: 'loading' })).toEqual({ state: 'loading' });
  });

  it('only sold out and unregistered products are blocked; unknown stock can be added', () => {
    expect(canAddToCart({ state: 'in-stock', available: 1 })).toBe(true);
    expect(canAddToCart({ state: 'unknown' })).toBe(true);
    expect(canAddToCart({ state: 'sold-out' })).toBe(false);
    expect(canAddToCart({ state: 'no-record' })).toBe(false);
    expect(canAddToCart({ state: 'loading' })).toBe(false);
    expect(knownUnits({ state: 'in-stock', available: 4 })).toBe(4);
    expect(knownUnits({ state: 'unknown' })).toBeNull();
  });
});
