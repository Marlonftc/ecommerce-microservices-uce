// How an order is shown. The six states are exactly those of order-service; EXPIRED is
// a cancel_reason of CANCELLED, never a state. inventory_status says whether units are
// still held, confirmed or already released, so nothing is claimed before it happened.
import type { CancelReason, OrderOut, OrderStatus, Simulation } from '../api/types';
import type { BadgeTone } from '../components/StatusBadge';

export const STATUS_LABELS: Record<OrderStatus, string> = {
  RESERVING: 'Reservando',
  PENDING_PAYMENT: 'Pendiente de pago',
  PAYMENT_PENDING: 'Pago en proceso',
  PAID: 'Pagado',
  PAYMENT_REJECTED: 'Pago rechazado',
  CANCELLED: 'Cancelado',
};

const STATUS_TONES: Record<OrderStatus, BadgeTone> = {
  RESERVING: 'info',
  PENDING_PAYMENT: 'warning',
  PAYMENT_PENDING: 'info',
  PAID: 'success',
  PAYMENT_REJECTED: 'danger',
  CANCELLED: 'neutral',
};

/** Reasons as the owner of the order reads them; see cancelReasonText for other viewers */
export const CANCEL_REASONS: Record<CancelReason, string> = {
  USER: 'Lo cancelaste tú.',
  EXPIRED: 'Venció el tiempo para pagar.',
  INSUFFICIENT_STOCK: 'No había unidades suficientes de un producto.',
  RESERVATION_TIMEOUT: 'El inventario no confirmó la reserva a tiempo.',
};

export const SIMULATION_LABELS: Record<Simulation, string> = {
  APPROVE: 'Aprobar el pago',
  REJECT: 'Rechazar el pago',
  SLOW_APPROVE: 'Aprobación lenta (se confirma en segundo plano)',
};

export const ACTIVE_STATUSES: OrderStatus[] = ['RESERVING', 'PENDING_PAYMENT', 'PAYMENT_PENDING'];

export function isActive(order: Pick<OrderOut, 'status'>): boolean {
  return ACTIVE_STATUSES.includes(order.status);
}

const holdsUnits = (order: OrderOut) => order.inventory_status === 'RESERVED' || order.inventory_status === 'PENDING';

/** Whether reserved units were released, are still waiting to be released, or never existed */
export function releaseText(order: OrderOut): string {
  if (order.inventory_status === 'RELEASED') return 'Las unidades reservadas se liberaron.';
  if (order.inventory_status === 'NOT_RESERVED') return 'No llegaron a reservarse unidades.';
  return 'La liberación de las unidades reservadas está pendiente; el sistema la completará automáticamente.';
}

/** Label and colour of the state: the same for every viewer */
export function statusBadge(order: Pick<OrderOut, 'status'>): { label: string; tone: BadgeTone } {
  return { label: STATUS_LABELS[order.status], tone: STATUS_TONES[order.status] };
}

/**
 * Cancellation reason. ownView must come from comparing order.user_id with the viewer
 * (isOwner), never from the role: an admin can also buy and cancel their own orders.
 */
export function cancelReasonText(reason: CancelReason | null, ownView: boolean): string {
  if (!reason) return 'Pedido cancelado.';
  if (reason === 'USER' && !ownView) return 'Lo canceló el cliente.';
  return CANCEL_REASONS[reason];
}

export interface OrderView {
  label: string;
  tone: BadgeTone;
  /** One sentence with what the state means, written for the person looking at it */
  detail: string;
}

/** ownView: the viewer is the owner of the order (see isOwner) */
export function describeOrder(order: OrderOut, ownView: boolean): OrderView {
  const { label, tone } = statusBadge(order);
  switch (order.status) {
    case 'RESERVING':
      return {
        label, tone,
        detail: ownView ? 'Estamos reservando tus productos en el inventario.' : 'Se están reservando los productos en el inventario.',
      };
    case 'PENDING_PAYMENT':
      return {
        label, tone,
        detail: ownView
          ? 'Las unidades están reservadas. Paga antes de que venza el plazo.'
          : 'Las unidades están reservadas a la espera del pago del cliente.',
      };
    case 'PAYMENT_PENDING':
      return {
        label, tone,
        detail: ownView
          ? 'El resultado del pago aún no se conoce. No vuelvas a pagar: se confirmará solo.'
          : 'El resultado del pago aún no se conoce; se confirmará automáticamente.',
      };
    case 'PAID':
      return order.inventory_status === 'CONFIRMED'
        ? { label, tone, detail: 'Compra completada.' }
        : { label, tone, detail: 'Pago aprobado. Falta confirmar las unidades en el inventario; se hará automáticamente.' };
    case 'PAYMENT_REJECTED':
      return { label, tone, detail: `El pago fue rechazado. ${releaseText(order)}` };
    case 'CANCELLED':
      return { label, tone, detail: `${cancelReasonText(order.cancel_reason, ownView)} ${releaseText(order)}` };
  }
}

/** The order still changes by itself (worker, executor): worth reading it again */
export function isSettling(order: OrderOut, now: number = Date.now()): boolean {
  if (order.status === 'RESERVING' || order.status === 'PAYMENT_PENDING') return true;
  if (order.status === 'PAID') return order.inventory_status !== 'CONFIRMED';
  if (order.status === 'PAYMENT_REJECTED' || order.status === 'CANCELLED') return holdsUnits(order);
  // Expired but not cancelled yet by the worker
  return order.status === 'PENDING_PAYMENT' && expiresAtMs(order) !== null && expiresAtMs(order)! <= now;
}

export function expiresAtMs(order: OrderOut): number | null {
  if (!order.expires_at) return null;
  const ms = Date.parse(order.expires_at);
  return Number.isNaN(ms) ? null : ms;
}

/** Only the owner pays or cancels, also when the viewer is an admin */
export function isOwner(order: OrderOut, userId: number | undefined): boolean {
  return userId !== undefined && order.user_id === userId;
}

export function canPay(order: OrderOut, userId: number | undefined, now: number = Date.now()): boolean {
  const expires = expiresAtMs(order);
  return isOwner(order, userId) && order.status === 'PENDING_PAYMENT' && (expires === null || expires > now);
}

export function canCancel(order: OrderOut, userId: number | undefined): boolean {
  return isOwner(order, userId) && order.status === 'PENDING_PAYMENT';
}

/** Short order number for lists: the first block of the UUID */
export function shortId(id: string): string {
  return id.slice(0, 8).toUpperCase();
}
