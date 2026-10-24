// user-service administration through the gateway: /api/users (admin only).
// Customers never call this module: the pages only use it when the viewer is an admin,
// and user-service checks the role again on every request.
import { http } from './http';
import type { Role, UserOut } from './types';

/** Largest number of ids GET /users?ids= accepts */
export const MAX_USER_IDS = 100;

/** Positive integer ids, without duplicates, in ascending order */
export function uniqueUserIds(ids: readonly number[]): number[] {
  return [...new Set(ids.filter((id) => Number.isInteger(id) && id > 0))].sort((a, b) => a - b);
}

/** GET /api/users?ids=1,2,3: existing users ordered by id; unknown ids are skipped */
export async function getUsersByIds(ids: readonly number[], signal?: AbortSignal): Promise<UserOut[]> {
  const unique = uniqueUserIds(ids);
  if (unique.length === 0 || unique.length > MAX_USER_IDS) {
    throw new Error(`getUsersByIds needs between 1 and ${MAX_USER_IDS} ids`);
  }
  return (await http.get<UserOut[]>('/users', { query: { ids: unique.join(',') }, signal })).data;
}

/** GET /api/users?limit&offset (admin): plain list ordered by id, no total and no search */
export async function listUsers(limit: number, offset: number, signal?: AbortSignal): Promise<UserOut[]> {
  return (await http.get<UserOut[]>('/users', { query: { limit, offset }, signal })).data;
}

/** Fields an admin can change on ANOTHER account; the final value is sent, so repeating it is harmless */
export type UserChange = { role: Role } | { is_active: boolean };

/** PATCH /api/users/{id} (admin): 409 CANNOT_MODIFY_SELF or LAST_ACTIVE_ADMIN, 403 if no longer admin */
export async function updateUser(id: number, change: UserChange, signal?: AbortSignal): Promise<UserOut> {
  if (!Number.isInteger(id) || id < 1) throw new Error('user ids are positive integers');
  return (await http.patch<UserOut>(`/users/${id}`, change, { signal })).data;
}
