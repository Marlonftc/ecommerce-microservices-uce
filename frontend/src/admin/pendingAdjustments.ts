// Stock adjustments whose outcome is not confirmed yet, kept in sessionStorage BEFORE the
// request is sent. A timeout, a network error or a reload then repeat exactly the same
// request: same Idempotency-Key and same body (product, delta, reason), so inventory-service
// applies it at most once. Only ids, the key and the body are stored (no token, no profile).
// A record belongs to one user and one product, and is removed only when the outcome is known.

export const PENDING_ADJUSTMENTS_KEY = 'ecommerce-uce.pending-adjustments';
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface PendingAdjustment {
  userId: number;
  productId: number;
  delta: number;
  /** Already trimmed, exactly as sent */
  reason: string;
  key: string;
  savedAt: number;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is PendingAdjustment {
  const v = value as PendingAdjustment;
  return typeof v === 'object' && v !== null && Number.isInteger(v.userId) && Number.isInteger(v.productId)
    && Number.isInteger(v.delta) && v.delta !== 0 && typeof v.reason === 'string'
    && typeof v.key === 'string' && v.key.length > 0 && typeof v.savedAt === 'number';
}

function read(): PendingAdjustment[] {
  const s = storage();
  if (!s) return [];
  try {
    const data: unknown = JSON.parse(s.getItem(PENDING_ADJUSTMENTS_KEY) ?? '[]');
    return Array.isArray(data) ? data.filter(isRecord).filter((r) => Date.now() - r.savedAt < MAX_AGE_MS) : [];
  } catch {
    return [];
  }
}

function write(records: PendingAdjustment[]): void {
  const s = storage();
  if (!s) return;
  try {
    if (records.length === 0) s.removeItem(PENDING_ADJUSTMENTS_KEY);
    else s.setItem(PENDING_ADJUSTMENTS_KEY, JSON.stringify(records));
  } catch {
    // Storage full or blocked: the adjustment still works, it just cannot survive a reload
  }
}

export function getPendingAdjustment(userId: number, productId: number): PendingAdjustment | null {
  return read().find((r) => r.userId === userId && r.productId === productId) ?? null;
}

export function savePendingAdjustment(record: Omit<PendingAdjustment, 'savedAt'>): PendingAdjustment {
  const saved = { ...record, savedAt: Date.now() };
  write([...read().filter((r) => !(r.userId === record.userId && r.productId === record.productId)), saved]);
  return saved;
}

/** Removes only the attempt with this key: an old answer never removes a newer attempt */
export function clearPendingAdjustment(userId: number, productId: number, key: string): void {
  write(read().filter((r) => !(r.userId === userId && r.productId === productId && r.key === key)));
}
