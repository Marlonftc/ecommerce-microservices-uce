// Failures of checkout and payment, in three groups:
//   business    the backend answered with a definitive no (409, 404, 422)
//   unavailable the backend answered that it could not do it, and did nothing
//   uncertain   no reliable answer: the operation may have been done; recover it with
//               the ORIGINAL Idempotency-Key, never with a new one
import { ApiError } from '../api/errors';

export type OrderProblemKind = 'business' | 'unavailable' | 'uncertain' | 'session' | 'other';

export interface OrderProblem {
  kind: OrderProblemKind;
  code: string | null;
  message: string;
  error: unknown;
}

const GATEWAY_CODES = new Set(['UPSTREAM_UNAVAILABLE', 'UPSTREAM_TIMEOUT']);

const CHECKOUT_MESSAGES: Record<string, string> = {
  CART_CHANGED: 'Tu carrito cambió desde que lo revisaste. Revísalo y confirma de nuevo.',
  CART_EMPTY: 'Tu carrito está vacío.',
  CART_LOCKED: 'Ya tienes una compra en curso. Termínala o cancélala antes de crear otra.',
  PRODUCT_NOT_AVAILABLE: 'Algunos productos ya no están disponibles. Quítalos del carrito para continuar.',
  INSUFFICIENT_STOCK: 'No hay unidades suficientes de un producto. El pedido se canceló y tu carrito no cambió.',
  IDEMPOTENCY_KEY_REUSED: 'Este intento de compra corresponde a otra versión del carrito. Revisa el carrito y confirma de nuevo.',
};

const PAYMENT_MESSAGES: Record<string, string> = {
  ORDER_NOT_PAYABLE: 'Este pedido ya no se puede pagar.',
  IDEMPOTENCY_KEY_REUSED: 'El pago de este pedido ya se inició desde otra pestaña o con otra opción. Consultamos su estado.',
};

function classify(error: unknown, messages: Record<string, string>, orderServiceUnavailable: (e: ApiError) => boolean): OrderProblem {
  if (!(error instanceof ApiError)) return { kind: 'other', code: null, message: 'Ocurrió un error inesperado.', error };
  const code = error.code;
  if (error.kind === 'timeout' || error.kind === 'network') {
    return { kind: 'uncertain', code, message: 'No recibimos la respuesta del servidor. La operación pudo haberse completado.', error };
  }
  if (code !== null && GATEWAY_CODES.has(code)) {
    return { kind: 'uncertain', code, message: 'El servicio no respondió a tiempo. La operación pudo haberse completado.', error };
  }
  if (error.status === 401) return { kind: 'session', code, message: 'Tu sesión terminó. Inicia sesión de nuevo.', error };
  if (error.status === 404) return { kind: 'business', code, message: 'No encontramos el pedido.', error };
  if (error.status === 409 || error.status === 422) {
    return { kind: 'business', code, message: (code && messages[code]) ?? error.message, error };
  }
  if (orderServiceUnavailable(error)) {
    return { kind: 'unavailable', code, message: error.message, error };
  }
  // 500, 504 and any other 5xx: the outcome is unknown
  if (error.status >= 500) {
    return { kind: 'uncertain', code, message: 'El servidor tuvo un problema. La operación pudo haberse completado.', error };
  }
  return { kind: 'other', code, message: error.message, error };
}

/**
 * Checkout: only a 503 sent by order-service itself is safe ("product-service is not
 * available"): it answers so before saving any order. A 502 can come after the order was
 * saved as RESERVING (e.g. an unexpected inventory answer), so it is uncertain.
 */
export function classifyCheckoutError(error: unknown): OrderProblem {
  const problem = classify(error, CHECKOUT_MESSAGES, (e) => e.status === 503);
  if (problem.kind === 'unavailable') {
    return { ...problem, message: 'El catálogo no está disponible en este momento. No se creó ningún pedido; inténtalo de nuevo.' };
  }
  return problem;
}

/** Payment: order-service never answers 502/503 by itself here, so any 5xx is uncertain */
export function classifyPaymentError(error: unknown): OrderProblem {
  return classify(error, PAYMENT_MESSAGES, () => false);
}

/** Extra fields of a 409, read safely */
export function extraString(error: unknown, field: string): string | null {
  if (!(error instanceof ApiError)) return null;
  const value = error.extra[field];
  return typeof value === 'string' ? value : null;
}

export function extraNumbers(error: unknown, field: string): number[] {
  if (!(error instanceof ApiError)) return [];
  const value = error.extra[field];
  if (typeof value === 'number') return [value];
  return Array.isArray(value) ? value.filter((v): v is number => typeof v === 'number') : [];
}
