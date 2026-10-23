// order-service pending operations (admin): /api/admin/operations. They are the saga steps
// that the order-worker repeats until they finish (payment, inventory, notification).
import { http } from './http';
import type { NumberedPage, OperationKind, OperationOut, OperationStatus, RetryOut, Uuid } from './types';

export interface OperationFilters {
  status?: OperationStatus;
  kind?: OperationKind;
  errorKind?: 'TRANSIENT' | 'PERMANENT';
  orderId?: Uuid;
}

/** GET /api/admin/operations: most recently updated first, with the total of all pages */
export async function listOperations(
  filters: OperationFilters,
  page: number,
  size: number,
  signal?: AbortSignal,
): Promise<NumberedPage<OperationOut>> {
  const query = { status: filters.status, kind: filters.kind, error_kind: filters.errorKind, order_id: filters.orderId, page, size };
  return (await http.get<NumberedPage<OperationOut>>('/admin/operations', { query, signal })).data;
}

/**
 * POST /api/admin/operations/{id}/retry: reactivates a FAILED operation that nobody holds and
 * runs it once. order-service decides: 409 OPERATION_NOT_RETRYABLE if it is not FAILED any more
 * or another runner holds it. Repeating the call cannot run it twice for the same failure.
 */
export async function retryOperation(id: number, signal?: AbortSignal): Promise<RetryOut> {
  if (!Number.isInteger(id) || id < 1) throw new Error('operation ids are positive integers');
  return (await http.post<RetryOut>(`/admin/operations/${id}/retry`, undefined, { signal })).data;
}
