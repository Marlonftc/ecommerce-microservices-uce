// @vitest-environment jsdom
// Rules of the product form (same as product-service), classification of adjustment
// failures and the store of unresolved adjustments.
import { beforeEach, describe, expect, it } from 'vitest';
import { ApiError } from '../api/errors';
import { product } from '../test/shop';
import {
  INVENTORY_PRODUCT_SERVICE_DOWN, KEY_REUSED, NOT_ENOUGH_UNITS, classifyAdjustmentError, isNoStockRecord, isUncertain,
} from './adminErrors';
import { PENDING_ADJUSTMENTS_KEY, clearPendingAdjustment, getPendingAdjustment, savePendingAdjustment } from './pendingAdjustments';
import { fieldErrorsFromIssues, matchesData, normalizeCategory, parsePrice, validateProductForm } from './productRules';

const http = (status: number, body: unknown) => ApiError.fromResponse(status, body, 'req-1', null);

describe('price', () => {
  it.each([
    ['899.99', '899.99'],
    ['899,99', '899.99'],
    ['12.5', '12.50'],
    ['12,5', '12.50'],
    ['7', '7.00'],
    ['0.10', '0.10'],
    ['0,01', '0.01'],
    ['007,5', '7.50'],
    [' 15.00 ', '15.00'],
    ['9999999999.99', '9999999999.99'], // 12 digits, the limit of the service
  ])('%s is sent as the exact text %s', (raw, expected) => {
    expect(parsePrice(raw)).toEqual({ ok: true, value: expected });
  });

  it.each([
    ['1.234', /ambiguo/],
    ['1,234', /ambiguo/],
    ['1.234,50', /ambiguo/],
    ['1,234.50', /ambiguo/],
    ['12.345', /ambiguo/],
    ['1234.567', /2 decimales/],
    ['0', /mayor que 0/],
    ['0.00', /mayor que 0/],
    ['-5', /mayor que 0/],
    ['99999999999', /10 dígitos/],
    ['1e5', /solo números/],
    ['$10', /solo números/],
    ['10 000', /solo números/],
    ['.5', /solo números/],
    ['5.', /solo números/],
    ['', /Ingresa el precio/],
  ])('%s is rejected', (raw, message) => {
    const result = parsePrice(raw);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(message);
  });
});

describe('product form', () => {
  const valid = { sku: ' lap-003 ', name: '  Laptop  ', description: ' Equipo ', category: 'Ropa  Deportiva', price: '899,9' };

  it('normalizes like product-service and keeps the price as text', () => {
    const result = validateProductForm(valid);
    expect(result).toEqual({
      ok: true,
      data: { sku: 'LAP-003', name: 'Laptop', description: 'Equipo', category: 'ropa-deportiva', price: '899.90' },
    });
    expect(normalizeCategory('  Ñandú  Azul ')).toBe('ñandú-azul');
  });

  it('rejects what the service rejects', () => {
    const result = validateProductForm({ sku: 'LÁP 3', name: 'L', description: 'x'.repeat(2001), category: 'a_b', price: '1.234' });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(Object.keys(result.errors).sort()).toEqual(['category', 'description', 'name', 'price', 'sku']);
    expect(validateProductForm({ ...valid, sku: 'AB' }).ok).toBe(false);
    expect(validateProductForm({ ...valid, sku: 'A'.repeat(33) }).ok).toBe(false);
    expect(validateProductForm({ ...valid, sku: 'LAP--3' }).ok).toBe(false);
    expect(validateProductForm({ ...valid, category: 'x' }).ok).toBe(false);
  });

  it('compares a saved product with the sent data, prices as exact decimals', () => {
    const saved = product(9, { sku: 'LAP-003', name: 'Laptop', description: 'Equipo', category: 'laptops', price: '899.90' });
    const data = { sku: 'LAP-003', name: 'Laptop', description: 'Equipo', category: 'laptops', price: '899.90' };
    expect(matchesData(saved, data)).toBe(true);
    expect(matchesData({ ...saved, price: '899.9' }, data)).toBe(true);
    expect(matchesData({ ...saved, price: '899.91' }, data)).toBe(false);
    expect(matchesData({ ...saved, name: 'Otra' }, data)).toBe(false);
  });

  it('maps 422 issues of FastAPI to the fields', () => {
    expect(fieldErrorsFromIssues([{ field: 'price', message: 'too many digits' }, { field: 'x.sku', message: 'bad' }]))
      .toEqual({ price: 'El servidor rechazó este valor: too many digits', sku: 'El servidor rechazó este valor: bad' });
  });
});

describe('adjustment failures', () => {
  it('definitive answers of inventory-service: nothing was applied', () => {
    expect(classifyAdjustmentError(http(409, { detail: NOT_ENOUGH_UNITS })).kind).toBe('not-applied');
    expect(classifyAdjustmentError(http(503, { detail: INVENTORY_PRODUCT_SERVICE_DOWN })).kind).toBe('not-applied');
    expect(classifyAdjustmentError(http(404, { detail: 'Product does not exist' })).kind).toBe('not-applied');
    expect(classifyAdjustmentError(http(422, { detail: [{ loc: ['body', 'delta'], msg: 'bad' }] })).kind).toBe('not-applied');
  });

  it('the gateway or an unknown 503 leave the outcome unknown', () => {
    expect(classifyAdjustmentError(http(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } })).kind).toBe('uncertain');
    expect(classifyAdjustmentError(http(504, { detail: { code: 'UPSTREAM_TIMEOUT', message: 'x' } })).kind).toBe('uncertain');
    expect(classifyAdjustmentError(http(503, { detail: 'Service Unavailable' })).kind).toBe('uncertain');
    expect(classifyAdjustmentError(http(503, '<html>busy</html>')).kind).toBe('uncertain');
    expect(classifyAdjustmentError(http(500, { detail: 'Internal Server Error' })).kind).toBe('uncertain');
    expect(classifyAdjustmentError(ApiError.network()).kind).toBe('uncertain');
    expect(classifyAdjustmentError(ApiError.timeout()).kind).toBe('uncertain');
  });

  it('a key used with other data is a conflict; 401 keeps the attempt for the session', () => {
    expect(classifyAdjustmentError(http(409, { detail: KEY_REUSED })).kind).toBe('key-conflict');
    expect(classifyAdjustmentError(http(401, { detail: 'Invalid token' })).kind).toBe('session');
  });

  it('unknown outcomes of product writes and the "no stock record" answer', () => {
    expect(isUncertain(ApiError.network())).toBe(true);
    expect(isUncertain(http(502, { detail: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' } }))).toBe(true);
    expect(isUncertain(http(409, { detail: 'SKU already exists' }))).toBe(false);
    expect(isNoStockRecord(http(404, { detail: 'No stock registered for this product' }))).toBe(true);
    expect(isNoStockRecord(http(404, { detail: { code: 'NOT_FOUND', message: 'Route not found' } }))).toBe(false);
  });
});

describe('pending adjustments', () => {
  beforeEach(() => sessionStorage.clear());

  it('belongs to one user and one product; an old key never removes a newer attempt', () => {
    savePendingAdjustment({ userId: 1, productId: 5, delta: 3, reason: 'ingreso', key: 'k1' });
    expect(getPendingAdjustment(1, 5)).toMatchObject({ delta: 3, reason: 'ingreso', key: 'k1' });
    expect(getPendingAdjustment(2, 5)).toBeNull();
    expect(getPendingAdjustment(1, 6)).toBeNull();
    savePendingAdjustment({ userId: 1, productId: 5, delta: -1, reason: '', key: 'k2' });
    clearPendingAdjustment(1, 5, 'k1');
    expect(getPendingAdjustment(1, 5)?.key).toBe('k2');
    clearPendingAdjustment(1, 5, 'k2');
    expect(sessionStorage.getItem(PENDING_ADJUSTMENTS_KEY)).toBeNull();
  });

  it('ignores corrupt data and stores no token', () => {
    sessionStorage.setItem(PENDING_ADJUSTMENTS_KEY, '{not json');
    expect(getPendingAdjustment(1, 5)).toBeNull();
    savePendingAdjustment({ userId: 1, productId: 5, delta: 2, reason: '', key: 'k' });
    expect(Object.keys(JSON.parse(sessionStorage.getItem(PENDING_ADJUSTMENTS_KEY)!)[0]).sort())
      .toEqual(['delta', 'key', 'productId', 'reason', 'savedAt', 'userId']);
  });
});
