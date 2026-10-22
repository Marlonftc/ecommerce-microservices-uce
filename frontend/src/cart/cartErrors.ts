// Classifies the failure of a cart change. The important line is between errors whose
// outcome is KNOWN (the backend answered: the cart did not change) and errors whose
// outcome is UNCERTAIN (no answer arrived: the change may have been applied).
import { ApiError } from '../api/errors';
import { MAX_QUANTITY } from './quantity';

/** Most different products a cart may hold (MAX_PRODUCTS in order-service) */
export const MAX_CART_PRODUCTS = 50;

export type CartProblemKind =
  | 'changed' //       409 CART_CHANGED: someone else changed the cart
  | 'locked' //        409 CART_LOCKED or locked=true: a checkout is in progress
  | 'full' //          409 CART_FULL
  | 'product-unavailable' // 404 PRODUCT_NOT_AVAILABLE
  | 'catalog-down' //  503/502 sent by order-service itself: product-service did not answer, cart unchanged
  | 'uncertain' //     timeout, network error, gateway 502/504, 500: the change may have happened
  | 'invalid' //       422 or a value refused before sending
  | 'session' //       401: AuthContext ends the session
  | 'busy' //          another change is still running: this one was not sent
  | 'needs-reload' //  the last outcome is unknown: the cart must be read again first
  | 'load-failed' //   the cart could not be read, so no version is known
  | 'cancelled' //     the session changed while the request ran: the answer is ignored
  | 'other';

export interface CartProblem {
  kind: CartProblemKind;
  /** Message for the user, in Spanish; empty for 'busy' (nothing to show) */
  message: string;
  error?: unknown;
}

export const CART_MESSAGES: Record<CartProblemKind, string> = {
  changed: 'Tu carrito cambió en otra pestaña o dispositivo. Revisa las cantidades y vuelve a intentarlo.',
  locked: 'Hay una compra en curso. El carrito no se puede modificar hasta que termine.',
  full: `Tu carrito ya tiene ${MAX_CART_PRODUCTS} productos distintos, el máximo permitido.`,
  'product-unavailable': 'Este producto ya no está disponible en el catálogo.',
  'catalog-down': 'No se pudo actualizar tu carrito porque el catálogo no responde. Tu carrito no cambió.',
  uncertain:
    'No recibimos la confirmación del servidor. El cambio pudo haberse aplicado, así que estamos consultando tu carrito antes de permitir otro cambio.',
  invalid: `La cantidad no es válida. Debe ser un número entero entre 1 y ${MAX_QUANTITY}.`,
  session: 'Tu sesión terminó. Inicia sesión de nuevo.',
  busy: '',
  'needs-reload': 'Primero debemos confirmar el estado de tu carrito. Pulsa «Reintentar» para consultarlo.',
  'load-failed': 'No pudimos consultar tu carrito. Inténtalo de nuevo en unos segundos.',
  cancelled: '',
  other: 'No se pudo actualizar tu carrito.',
};

/** Problems that need no message: nothing was sent, or the answer belongs to an old session */
export function isSilent(found: CartProblem): boolean {
  return found.kind === 'busy' || found.kind === 'cancelled';
}

/**
 * Message after a failed change. `reloaded` says whether the cart was read again
 * afterwards, which matters when the outcome of the change was uncertain.
 */
export function describeProblem(found: CartProblem, reloaded: boolean): string {
  if (found.kind === 'uncertain') {
    return reloaded
      ? 'No recibimos la confirmación del servidor, así que volvimos a consultar tu carrito. Revisa las cantidades antes de repetir el cambio.'
      : 'No recibimos la confirmación del servidor y tampoco pudimos consultar tu carrito. Pulsa «Reintentar» antes de hacer otro cambio.';
  }
  return found.message;
}

export function problem(kind: CartProblemKind, error?: unknown): CartProblem {
  return { kind, message: CART_MESSAGES[kind], error };
}

/** Gateway codes: nginx could not reach order-service or got no answer in time */
const GATEWAY_CODES = new Set(['UPSTREAM_UNAVAILABLE', 'UPSTREAM_TIMEOUT']);

export function classifyCartError(error: unknown): CartProblem {
  if (!(error instanceof ApiError)) return problem('other', error);
  if (error.kind === 'timeout' || error.kind === 'network') return problem('uncertain', error);

  switch (error.code) {
    case 'CART_CHANGED':
      return problem('changed', error);
    case 'CART_LOCKED':
      return problem('locked', error);
    case 'CART_FULL':
      return problem('full', error);
    case 'PRODUCT_NOT_AVAILABLE':
      return problem('product-unavailable', error);
    default:
      break;
  }
  if (error.code !== null && GATEWAY_CODES.has(error.code)) return problem('uncertain', error);
  if (error.status === 504 || error.status === 500) return problem('uncertain', error);
  // order-service answers 503/502 itself (detail text, no code) when product-service
  // could not validate the product; it does that BEFORE touching the cart
  if (error.status === 503 || error.status === 502) return problem('catalog-down', error);
  if (error.status === 422) return problem('invalid', error);
  if (error.status === 401) return problem('session', error);
  return { kind: 'other', message: error.message, error };
}
