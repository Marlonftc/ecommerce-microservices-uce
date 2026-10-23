// Test data shaped like OrderOut of order-service.
import type { OrderOut } from '../api/types';

export const ORDER_ID = '0b9a6c1e-4f2d-4c8a-9e3b-7d5f1a2b3c4d';
export const OTHER_ORDER_ID = 'f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b';

export function order(overrides: Partial<OrderOut> = {}): OrderOut {
  return {
    id: ORDER_ID,
    user_id: 7,
    status: 'PENDING_PAYMENT',
    inventory_status: 'RESERVED',
    cancel_reason: null,
    payment_status: null,
    total: '1519.48',
    currency: 'USD',
    cart_version: 5,
    expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    created_at: '2026-10-22T20:00:00Z',
    updated_at: '2026-10-22T20:00:00Z',
    items: [
      { product_id: 1, sku: 'LAP-001', name: 'Laptop 14 pulgadas', unit_price: '749.99', quantity: 2, subtotal: '1499.98' },
      { product_id: 5, sku: 'MOU-001', name: 'Mouse inalámbrico', unit_price: '19.50', quantity: 1, subtotal: '19.50' },
    ],
    ...overrides,
  };
}
