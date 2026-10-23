// Quantities accepted by the cart: whole numbers from 1 to 99 (CartItemIn in
// order-service), and never more than the known stock.

export const MIN_QUANTITY = 1;
export const MAX_QUANTITY = 99;

export type QuantityCheck = { ok: true; value: number } | { ok: false; message: string };

/**
 * Validates what the user typed. Only plain digits are accepted, so "1.5", "1e2",
 * "0x10" or "abc" are rejected instead of being converted by Number().
 * `max` is the highest value allowed here (e.g. units left in stock).
 */
export function parseQuantity(raw: string, max: number = MAX_QUANTITY): QuantityCheck {
  const text = raw.trim();
  if (text === '') return { ok: false, message: 'Ingresa una cantidad.' };
  if (/^-\d+$/.test(text)) return { ok: false, message: 'La cantidad mínima es 1.' };
  if (!/^\d+$/.test(text)) return { ok: false, message: 'La cantidad debe ser un número entero.' };
  const value = Number(text);
  if (value < MIN_QUANTITY) return { ok: false, message: 'La cantidad mínima es 1.' };
  const limit = Math.min(MAX_QUANTITY, Math.max(0, Math.floor(max)));
  if (value > limit) {
    if (limit < MIN_QUANTITY) return { ok: false, message: 'No puedes agregar más unidades de este producto.' };
    return {
      ok: false,
      message: limit < MAX_QUANTITY ? `Puedes agregar como máximo ${limit}.` : `La cantidad máxima por producto es ${MAX_QUANTITY}.`,
    };
  }
  return { ok: true, value };
}

/**
 * How many more units of a product can go into the cart: limited by the 99 per
 * product of order-service and, when it is known, by the available stock.
 */
export function unitsLeftToAdd(inCart: number, available: number | null): number {
  const ceiling = available === null ? MAX_QUANTITY : Math.min(MAX_QUANTITY, available);
  return Math.max(0, ceiling - inCart);
}
