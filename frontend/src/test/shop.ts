// Test data shaped like product-service, inventory-service and the cart of order-service.
import type { CartItemOut, CartOut, ProductOut } from '../api/types';
import { SESSION_KEY } from '../auth/session';
import { json, type FakeApi } from './fakeApi';
import { makeToken } from './tokens';

export function product(id: number, overrides: Partial<ProductOut> = {}): ProductOut {
  return {
    id,
    sku: `SKU-${String(id).padStart(3, '0')}`,
    name: `Producto ${id}`,
    description: '',
    category: 'accesorios',
    price: '10.00',
    is_active: true,
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    ...overrides,
  };
}

export function page(items: ProductOut[], total = items.length, limit = 12, offset = 0) {
  return { items, total, limit, offset };
}

/** Exact cents of a decimal string, for building fixtures (the app never does this) */
function cents(value: string): number {
  const [whole = '0', fraction = ''] = value.split('.');
  return Number(whole) * 100 + Number(fraction.padEnd(2, '0').slice(0, 2));
}

function decimal(totalCents: number): string {
  return `${Math.floor(totalCents / 100)}.${String(totalCents % 100).padStart(2, '0')}`;
}

export interface LineSpec {
  id: number;
  quantity: number;
  price?: string;
  name?: string;
  /** false: the product was deactivated after it was added */
  available?: boolean;
}

/** A priced cart, as GET /cart returns it when product-service answers */
export function cart(version: number, lines: LineSpec[] = [], extra: Partial<CartOut> = {}): CartOut {
  let total = 0;
  const items: CartItemOut[] = lines.map((line) => {
    if (line.available === false) {
      return { product_id: line.id, quantity: line.quantity, sku: null, name: null, unit_price: null, subtotal: null, available: false };
    }
    const price = line.price ?? '10.00';
    const subtotal = cents(price) * line.quantity;
    total += subtotal;
    return {
      product_id: line.id,
      quantity: line.quantity,
      sku: `SKU-${String(line.id).padStart(3, '0')}`,
      name: line.name ?? `Producto ${line.id}`,
      unit_price: price,
      subtotal: decimal(subtotal),
      available: true,
    };
  });
  return { version, locked: false, items, prices_available: true, estimated_total: decimal(total), currency: 'USD', ...extra };
}

/** The same cart when product-service does not answer: quantities only */
export function unpricedCart(version: number, lines: { id: number; quantity: number }[]): CartOut {
  return {
    version,
    locked: false,
    items: lines.map((line) => ({
      product_id: line.id, quantity: line.quantity, sku: null, name: null, unit_price: null, subtotal: null, available: null,
    })),
    prices_available: false,
    estimated_total: null,
    currency: 'USD',
  };
}

export const customer = {
  id: 7, email: 'ana@example.com', full_name: 'Ana Pérez', role: 'customer' as const, is_active: true, created_at: '2026-10-01T10:00:00Z',
};
export const otherCustomer = { ...customer, id: 8, email: 'luis@example.com', full_name: 'Luis Mora' };
export const admin = { ...customer, id: 1, email: 'admin@example.com', full_name: 'Admin UCE', role: 'admin' as const };

/** Stores a valid session in this tab and makes /users/me answer with the profile */
export function signIn(api: FakeApi, user: typeof customer | typeof admin = customer): string {
  const token = makeToken({ sub: String(user.id), role: user.role }, `sig-${user.id}`);
  const exp = JSON.parse(Buffer.from(token.split('.')[1]!, 'base64url').toString()).exp as number;
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ token, expiresAt: exp * 1000 }));
  api.on('GET', '/api/users/me', () => json(200, user));
  return token;
}

/** Stock rows for GET /inventory; products left out have no stock record */
export function stock(rows: Record<number, number>) {
  return Object.entries(rows).map(([id, available]) => ({ product_id: Number(id), available }));
}
