import { describe, expect, it } from 'vitest';
import { MAX_QUANTITY, parseQuantity, unitsLeftToAdd } from './quantity';

describe('parseQuantity', () => {
  it.each(['1', '7', ' 3 ', '99', '007'])('accepts %j', (raw) => {
    const result = parseQuantity(raw);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value).toBe(Number(raw.trim()));
  });

  it.each([
    ['', 'Ingresa una cantidad.'],
    ['   ', 'Ingresa una cantidad.'],
    ['0', 'La cantidad mínima es 1.'],
    ['-1', 'La cantidad mínima es 1.'],
    ['1.5', 'La cantidad debe ser un número entero.'],
    ['1e2', 'La cantidad debe ser un número entero.'],
    ['0x10', 'La cantidad debe ser un número entero.'],
    ['abc', 'La cantidad debe ser un número entero.'],
    ['+3', 'La cantidad debe ser un número entero.'],
    ['100', 'La cantidad máxima por producto es 99.'],
  ])('rejects %j', (raw, message) => {
    expect(parseQuantity(raw)).toEqual({ ok: false, message });
  });

  it('never allows more than the given limit (units left in stock)', () => {
    expect(parseQuantity('5', 4)).toEqual({ ok: false, message: 'Puedes agregar como máximo 4.' });
    expect(parseQuantity('4', 4)).toEqual({ ok: true, value: 4 });
    expect(parseQuantity('1', 0)).toEqual({ ok: false, message: 'No puedes agregar más unidades de este producto.' });
  });

  it('the limit can never go above 99', () => {
    expect(parseQuantity('100', 500).ok).toBe(false);
  });
});

describe('unitsLeftToAdd', () => {
  it('is limited by 99 per product when the stock is unknown', () => {
    expect(unitsLeftToAdd(0, null)).toBe(MAX_QUANTITY);
    expect(unitsLeftToAdd(98, null)).toBe(1);
    expect(unitsLeftToAdd(99, null)).toBe(0);
  });

  it('is limited by the known stock', () => {
    expect(unitsLeftToAdd(2, 5)).toBe(3);
    expect(unitsLeftToAdd(5, 5)).toBe(0);
    expect(unitsLeftToAdd(7, 5)).toBe(0);
    expect(unitsLeftToAdd(0, 500)).toBe(MAX_QUANTITY);
  });
});
