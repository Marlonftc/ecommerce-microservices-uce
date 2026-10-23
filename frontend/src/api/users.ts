// user-service administration through the gateway: /api/users (admin only).
// Customers never call this module: the pages only use it when the viewer is an admin,
// and user-service checks the role again on every request.
import { http } from './http';
import type { UserOut } from './types';

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
