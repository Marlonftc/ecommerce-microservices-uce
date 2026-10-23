// order-service cart through the gateway: /api/cart. Every call needs the token.
// expected_version travels in the JSON body for PUT and in the query for DELETE,
// exactly as the cart router declares it.
import { http } from './http';
import type { CartItemIn, CartOut } from './types';

const MAX_PRODUCT_ID = 2_147_483_647;

function itemPath(productId: number): string {
  if (!Number.isInteger(productId) || productId < 1 || productId > MAX_PRODUCT_ID) {
    throw new Error('productId must be an integer between 1 and 2147483647');
  }
  return `/cart/items/${productId}`;
}

/** GET /api/cart */
export async function getCart(signal?: AbortSignal): Promise<CartOut> {
  return (await http.get<CartOut>('/cart', { signal })).data;
}

/** PUT /api/cart/items/{id}: sets the ABSOLUTE quantity (1-99), it does not add to it */
export async function setItem(
  productId: number,
  quantity: number,
  expectedVersion: number,
  signal?: AbortSignal,
): Promise<CartOut> {
  const body: CartItemIn = { quantity, expected_version: expectedVersion };
  return (await http.put<CartOut>(itemPath(productId), body, { signal })).data;
}

/** DELETE /api/cart/items/{id}?expected_version= (idempotent) */
export async function removeItem(productId: number, expectedVersion: number, signal?: AbortSignal): Promise<CartOut> {
  return (await http.delete<CartOut>(itemPath(productId), { query: { expected_version: expectedVersion }, signal })).data;
}

/** DELETE /api/cart?expected_version= */
export async function clearCart(expectedVersion: number, signal?: AbortSignal): Promise<CartOut> {
  return (await http.delete<CartOut>('/cart', { query: { expected_version: expectedVersion }, signal })).data;
}
