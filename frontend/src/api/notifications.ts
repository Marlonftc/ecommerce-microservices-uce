// notification-service through the gateway: /api/notifications. A customer receives only
// their own notifications. Titles and messages are built by the service and are shown as
// plain text, never interpreted as HTML.
import { http } from './http';
import type { NotificationOut, NumberedPage, Uuid } from './types';
import { isOrderId } from './orders';

/** GET /api/notifications?page&size&unread_only: newest first, with the total of all pages */
export async function listNotifications(
  page: number,
  size: number,
  unreadOnly: boolean,
  signal?: AbortSignal,
): Promise<NumberedPage<NotificationOut>> {
  const query = { page, size, unread_only: unreadOnly ? true : undefined };
  return (await http.get<NumberedPage<NotificationOut>>('/notifications', { query, signal })).data;
}

/** Number of unread notifications: the total field of a one-item page */
export async function countUnread(signal?: AbortSignal): Promise<number> {
  return (await listNotifications(1, 1, true, signal)).total;
}

/** PATCH /api/notifications/{id}/read: marking it again keeps the first read date */
export async function markNotificationRead(id: Uuid, signal?: AbortSignal): Promise<NotificationOut> {
  // Notification ids are UUIDs, like order ids
  if (!isOrderId(id)) throw new Error('notification ids are UUIDs');
  return (await http.patch<NotificationOut>(`/notifications/${id}/read`, undefined, { signal })).data;
}
