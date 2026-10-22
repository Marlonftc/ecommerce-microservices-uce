// Types of the public HTTP contracts, taken from the FastAPI routers and Pydantic
// schemas of the six services. All paths are relative to the gateway prefix /api.
//
// Conventions of the backend:
// - Decimal amounts (prices, totals) arrive as JSON strings, e.g. "899.99", to stay exact.
// - Dates arrive as ISO 8601 strings.
// - User and product ids are integers; orders, payments and notifications use UUIDs.

export type Uuid = string;
export type IsoDateTime = string;
export type DecimalString = string;

// --- Pagination ---------------------------------------------------------------
// Two styles exist in the backend and they are kept as they are:
/** product-service: GET /products */
export interface OffsetPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
/** order-service (orders, admin operations) and notification-service */
export interface NumberedPage<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}
// user-service GET /users, payment-service GET /payments, inventory GET /inventory and
// GET /inventory/{id}/adjustments return plain arrays (limit/offset only, no total).

// --- user-service: /api/auth, /api/users ----------------------------------------
export type Role = 'customer' | 'admin';

/** POST /api/auth/register (unknown fields are rejected with 422) */
export interface RegisterRequest {
  email: string;
  full_name: string; // 2-120 characters
  password: string; // 8-72 characters
}
/** POST /api/auth/login */
export interface LoginRequest {
  email: string;
  password: string;
}
export interface TokenResponse {
  access_token: string;
  token_type: string; // "bearer"
  expires_in: number; // seconds
}
/** POST /api/auth/register (201), GET /api/users/me, GET /api/users (admin) */
export interface UserOut {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  created_at: IsoDateTime;
}

// --- product-service: /api/products ----------------------------------------------
// GET /api/products?q&category&ids&limit(<=100)&offset&include_inactive(admin) -> OffsetPage<ProductOut>
// GET /api/products/{id}; POST /api/products (admin, 201);
// PUT /api/products/{id} (admin, full replacement); DELETE /api/products/{id} (admin, 204, deactivates)
export interface ProductOut {
  id: number;
  sku: string;
  name: string;
  description: string;
  category: string;
  price: DecimalString;
  is_active: boolean;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}
export interface ProductIn {
  sku: string; // 3-32
  name: string; // 2-120
  description?: string; // up to 2000
  category: string; // 2-50
  price: DecimalString; // > 0, 2 decimals
}
/** PUT /api/products/{id}: is_active lets the admin reactivate a product */
export interface ProductUpdate extends ProductIn {
  is_active?: boolean;
}

// --- inventory-service: /api/inventory -------------------------------------------
// GET /api/inventory?ids=1,2,3 -> StockView[]; GET /api/inventory/{product_id} -> StockView
/** Anonymous users and customers */
export interface StockPublic {
  product_id: number;
  available: number;
}
/** Admin view of the same endpoints */
export interface StockAdmin extends StockPublic {
  reserved: number;
  updated_at: IsoDateTime;
}
export type StockView = StockPublic | StockAdmin;
/** POST /api/inventory/{product_id}/adjustments (admin, header Idempotency-Key) */
export interface AdjustmentIn {
  delta: number; // -100000..100000, not 0
  reason?: string; // up to 200
}
/** 201 new, 200 repeated with the same key; GET .../adjustments?limit&offset -> AdjustmentOut[] */
export interface AdjustmentOut {
  id: number;
  product_id: number;
  delta: number;
  available_before: number;
  available_after: number;
  admin_user_id: number | null;
  reason: string;
  created_at: IsoDateTime;
}

// --- order-service: /api/cart ----------------------------------------------------
// GET    /api/cart
// PUT    /api/cart/items/{product_id}                 body: CartItemIn (expected_version in the BODY)
// DELETE /api/cart/items/{product_id}?expected_version (expected_version as QUERY parameter)
// DELETE /api/cart?expected_version                    (expected_version as QUERY parameter)
// Errors: 409 CART_LOCKED | CART_CHANGED | CART_FULL, 404 PRODUCT_NOT_AVAILABLE, 503
export interface CartItemIn {
  quantity: number; // absolute quantity 1-99, not an increment
  expected_version?: number;
}
export interface CartItemOut {
  product_id: number;
  quantity: number;
  sku: string | null; // null while product-service does not answer
  name: string | null;
  unit_price: DecimalString | null;
  subtotal: DecimalString | null;
  available: boolean | null; // false if the product was deactivated
}
export interface CartOut {
  version: number;
  locked: boolean; // true while a checkout is in progress
  items: CartItemOut[];
  prices_available: boolean;
  estimated_total: DecimalString | null;
  currency: string;
}

// --- order-service: /api/orders --------------------------------------------------
export type OrderStatus =
  | 'RESERVING'
  | 'PENDING_PAYMENT'
  | 'PAYMENT_PENDING'
  | 'PAID'
  | 'PAYMENT_REJECTED'
  | 'CANCELLED';
export type InventoryStatus = 'PENDING' | 'RESERVED' | 'CONFIRMED' | 'RELEASED' | 'NOT_RESERVED';
export type PaymentStatus = 'REQUESTED' | 'APPROVED' | 'REJECTED';
export type CancelReason = 'USER' | 'EXPIRED' | 'INSUFFICIENT_STOCK' | 'RESERVATION_TIMEOUT';
export type Simulation = 'APPROVE' | 'REJECT' | 'SLOW_APPROVE';

/** POST /api/orders (header Idempotency-Key): 201 new, 200 same key, 202 RESERVING */
export interface CheckoutIn {
  cart_version: number;
}
/** POST /api/orders/{id}/pay (header Idempotency-Key): 200 final result, 202 PAYMENT_PENDING */
export interface PaymentIn {
  simulation: Simulation;
}
export interface OrderItemOut {
  product_id: number;
  sku: string;
  name: string;
  unit_price: DecimalString;
  quantity: number;
  subtotal: DecimalString;
}
/** GET /api/orders?page&size -> NumberedPage<OrderOut>; GET /api/orders/{id};
 *  POST /api/orders/{id}/cancel (409 ORDER_NOT_CANCELLABLE) */
export interface OrderOut {
  id: Uuid;
  user_id: number;
  status: OrderStatus;
  inventory_status: InventoryStatus;
  cancel_reason: CancelReason | null;
  payment_status: PaymentStatus | null;
  total: DecimalString;
  currency: string;
  cart_version: number;
  expires_at: IsoDateTime | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  items: OrderItemOut[];
}

// --- order-service: /api/admin/operations (admin) ----------------------------------
// GET /api/admin/operations?status&kind&error_kind&order_id&page&size -> NumberedPage<OperationOut>
// POST /api/admin/operations/{id}/retry -> RetryOut (409 OPERATION_NOT_RETRYABLE)
export type OperationKind = 'RESOLVE_PAYMENT' | 'CONFIRM_INVENTORY' | 'RELEASE_INVENTORY' | 'SEND_NOTIFICATION';
export type OperationStatus = 'PENDING' | 'DONE' | 'FAILED';
export interface OperationOut {
  id: number;
  order_id: Uuid;
  kind: OperationKind;
  operation_key: string;
  status: OperationStatus;
  attempts: number;
  error_kind: 'TRANSIENT' | 'PERMANENT' | null;
  last_error: string | null;
  next_attempt_at: IsoDateTime;
  locked_until: IsoDateTime | null;
  payload: Record<string, unknown>;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  completed_at: IsoDateTime | null;
}
export interface RetryOut {
  outcome: 'DONE' | 'RETRY' | 'FAILED' | 'SKIPPED' | 'LOST';
  operation: OperationOut;
}

// --- payment-service: /api/payments -----------------------------------------------
// GET /api/payments?limit&offset -> PaymentOut[]; GET /api/payments/{id}
export interface PaymentOut {
  id: Uuid;
  order_id: Uuid;
  user_id: number;
  amount: DecimalString;
  currency: string;
  status: string;
  simulation: Simulation;
  failure_reason: string | null;
  processed_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

// --- notification-service: /api/notifications ----------------------------------------
// GET /api/notifications?page&size&unread_only -> NumberedPage<NotificationOut>
// PATCH /api/notifications/{id}/read -> NotificationOut
export type NotificationType = 'ORDER_PAID' | 'PAYMENT_REJECTED' | 'ORDER_CANCELLED';
export interface NotificationOut {
  id: Uuid;
  event_id: string;
  user_id: number;
  order_id: Uuid;
  type: NotificationType;
  amount: DecimalString | null;
  currency: string | null;
  title: string;
  message: string;
  is_read: boolean;
  read_at: IsoDateTime | null;
  created_at: IsoDateTime;
}
