// Checkouts and payments whose outcome is not confirmed yet, kept in sessionStorage so
// that a timeout, a network error or a page reload can repeat the SAME request with
// the SAME Idempotency-Key. No password, token or profile data is stored here: only
// ids, the key and what is needed to repeat the request exactly. Records belong to one
// user and are removed as soon as the result is known.
import type { Simulation } from '../api/types';

export const PENDING_KEY = 'ecommerce-uce.pending-operations';
/** Older records are dropped: by then the backend has settled or cancelled the order */
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface PendingCheckout {
  userId: number;
  cartVersion: number;
  key: string;
  /** Known once POST /orders answered (e.g. 202 RESERVING) */
  orderId: string | null;
  savedAt: number;
}

export interface PendingPayment {
  userId: number;
  orderId: string;
  key: string;
  simulation: Simulation;
  savedAt: number;
}

interface Store {
  checkouts: PendingCheckout[];
  payments: PendingPayment[];
}

const SIMULATIONS: Simulation[] = ['APPROVE', 'REJECT', 'SLOW_APPROVE'];

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function isCheckout(value: unknown): value is PendingCheckout {
  const v = value as PendingCheckout;
  return typeof v === 'object' && v !== null && Number.isInteger(v.userId) && Number.isInteger(v.cartVersion)
    && typeof v.key === 'string' && v.key.length > 0 && (v.orderId === null || typeof v.orderId === 'string')
    && typeof v.savedAt === 'number';
}

function isPayment(value: unknown): value is PendingPayment {
  const v = value as PendingPayment;
  return typeof v === 'object' && v !== null && Number.isInteger(v.userId) && typeof v.orderId === 'string'
    && typeof v.key === 'string' && v.key.length > 0 && SIMULATIONS.includes(v.simulation) && typeof v.savedAt === 'number';
}

function read(): Store {
  const empty: Store = { checkouts: [], payments: [] };
  const s = storage();
  if (!s) return empty;
  try {
    const raw = s.getItem(PENDING_KEY);
    if (!raw) return empty;
    const data = JSON.parse(raw) as Partial<Store>;
    const fresh = (r: { savedAt: number }) => Date.now() - r.savedAt < MAX_AGE_MS;
    return {
      checkouts: Array.isArray(data.checkouts) ? data.checkouts.filter(isCheckout).filter(fresh) : [],
      payments: Array.isArray(data.payments) ? data.payments.filter(isPayment).filter(fresh) : [],
    };
  } catch {
    return empty; // corrupt data is ignored (and replaced on the next write)
  }
}

function write(store: Store): void {
  const s = storage();
  if (!s) return;
  try {
    if (store.checkouts.length === 0 && store.payments.length === 0) s.removeItem(PENDING_KEY);
    else s.setItem(PENDING_KEY, JSON.stringify(store));
  } catch {
    // Storage full or blocked: the operation still works, it just cannot survive a reload
  }
}

/** A new random key for a NEW operation; never used to recover an unknown outcome */
export function newKey(): string {
  // randomUUID exists only in secure contexts (https, localhost); getRandomValues everywhere
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8]! & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function getCheckout(userId: number): PendingCheckout | null {
  return read().checkouts.find((r) => r.userId === userId) ?? null;
}

export function saveCheckout(record: Omit<PendingCheckout, 'savedAt'>): PendingCheckout {
  const saved = { ...record, savedAt: Date.now() };
  const store = read();
  write({ ...store, checkouts: [...store.checkouts.filter((r) => r.userId !== record.userId), saved] });
  return saved;
}

export function clearCheckout(userId: number, key?: string): void {
  const store = read();
  // With a key, only that attempt is removed (a newer one is never removed by an old answer)
  write({ ...store, checkouts: store.checkouts.filter((r) => r.userId !== userId || (key !== undefined && r.key !== key)) });
}

export function getPayment(userId: number, orderId: string): PendingPayment | null {
  return read().payments.find((r) => r.userId === userId && r.orderId === orderId) ?? null;
}

export function savePayment(record: Omit<PendingPayment, 'savedAt'>): PendingPayment {
  const saved = { ...record, savedAt: Date.now() };
  const store = read();
  write({
    ...store,
    payments: [...store.payments.filter((r) => !(r.userId === record.userId && r.orderId === record.orderId)), saved],
  });
  return saved;
}

export function clearPayment(userId: number, orderId: string): void {
  const store = read();
  write({ ...store, payments: store.payments.filter((r) => !(r.userId === userId && r.orderId === orderId)) });
}
