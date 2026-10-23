// How the admin pages read failures of product-service and inventory-service.
//
// Stock adjustment (POST /inventory/{id}/adjustments). inventory-service first looks for
// the key (a repeated key returns the first result), then asks product-service whether the
// product exists, and only then changes the stock in one transaction. So:
//   not-applied   the service answered a definitive no; nothing changed:
//                 409 "Not enough available units", 404, 422, 403, and the 503 that
//                 inventory-service itself sends when product-service is down
//   key-conflict  409 "Idempotency-Key already used with different data": that key was
//                 applied with another body; the attempt is NOT dropped silently
//   uncertain     timeout, network error, gateway errors (UPSTREAM_*), 500, or a 503
//                 without the inventory message: it may have been applied
//   session       401: the session ended; the attempt stays saved for this user
// The gateway never rewrites the services' own errors (proxy_intercept_errors is off) and
// turns its own 503 into 502 UPSTREAM_UNAVAILABLE, so the inventory 503 is recognisable.
import { ApiError } from '../api/errors';

export const INVENTORY_PRODUCT_SERVICE_DOWN = 'product-service is not available, try again later';
export const NOT_ENOUGH_UNITS = 'Not enough available units for this adjustment';
export const KEY_REUSED = 'Idempotency-Key already used with different data';
export const NO_STOCK_RECORD = 'No stock registered for this product';

export type AdjustmentProblemKind = 'not-applied' | 'key-conflict' | 'uncertain' | 'session';

export interface AdjustmentProblem {
  kind: AdjustmentProblemKind;
  message: string;
}

export function classifyAdjustmentError(error: unknown): AdjustmentProblem {
  if (!(error instanceof ApiError) || error.kind !== 'http') {
    return { kind: 'uncertain', message: 'No recibimos la respuesta de inventory-service. El ajuste pudo haberse aplicado.' };
  }
  const { status, detail, code } = error;
  if (status === 401) return { kind: 'session', message: 'Tu sesión terminó. El ajuste queda guardado para revisarlo al volver a iniciar sesión.' };
  if (status === 409 && detail === NOT_ENOUGH_UNITS) {
    return { kind: 'not-applied', message: 'No se aplicó: no hay tantas unidades disponibles para quitar (las reservadas no se pueden quitar).' };
  }
  if (status === 409 && detail === KEY_REUSED) {
    return { kind: 'key-conflict', message: 'La clave de este ajuste ya se usó con otros datos. Revisa el historial antes de descartar el intento.' };
  }
  if (status === 503 && code === null && detail === INVENTORY_PRODUCT_SERVICE_DOWN) {
    return { kind: 'not-applied', message: 'No se aplicó: inventory-service no pudo comprobar el producto porque product-service no está disponible.' };
  }
  if (status === 404) return { kind: 'not-applied', message: 'No se aplicó: el producto no existe.' };
  if (status === 422) return { kind: 'not-applied', message: 'No se aplicó: el servidor rechazó los datos del ajuste.' };
  if (status === 403) return { kind: 'not-applied', message: 'No se aplicó: tu cuenta no tiene permisos de administrador.' };
  return { kind: 'uncertain', message: 'El servicio no confirmó el resultado. El ajuste pudo haberse aplicado.' };
}

/** A write to product-service with an unknown outcome (it may have been saved) */
export function isUncertain(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.kind !== 'http') return true;
  return error.status >= 500 || error.code === 'UPSTREAM_UNAVAILABLE' || error.code === 'UPSTREAM_TIMEOUT';
}

/** GET /inventory/{id} answered that the product has no stock record (not an outage) */
export function isNoStockRecord(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'http' && error.status === 404 && error.detail === NO_STOCK_RECORD;
}
