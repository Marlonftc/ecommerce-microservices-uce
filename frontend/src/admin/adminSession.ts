// A 403 on an admin page can mean that this account lost the admin role while the page
// was open (user-service reads the role from its database on every request). The session
// is then checked again with GET /api/users/me: AuthContext ends it with "role-changed"
// when the profile and the role of the token no longer match, which also removes every
// admin page and stops their requests. If the account is still an admin, the page loads again.
import { useCallback } from 'react';
import { ApiError } from '../api/errors';
import { useAuth } from '../auth/AuthContext';

export function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'http' && error.status === 403;
}

/** Returns a handler: true when the error was a 403 and the session is being checked again */
export function useForbiddenCheck(): (error: unknown) => boolean {
  const { retry } = useAuth();
  return useCallback(
    (error: unknown) => {
      if (!isForbidden(error)) return false;
      retry();
      return true;
    },
    [retry],
  );
}
