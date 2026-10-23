// user-service through the gateway: /api/auth and /api/users/me
import { http } from './http';
import type { LoginRequest, RegisterRequest, TokenResponse, UserOut } from './types';

/** POST /api/auth/login, sent without a token. 401 = wrong credentials or inactive account. */
export async function login(credentials: LoginRequest): Promise<TokenResponse> {
  return (await http.post<TokenResponse>('/auth/login', credentials, { auth: false })).data;
}

/** POST /api/auth/register, sent without a token. 201 = created, 409 = email already registered. */
export async function register(data: RegisterRequest): Promise<UserOut> {
  return (await http.post<UserOut>('/auth/register', data, { auth: false })).data;
}

/** GET /api/users/me with the current token: the verified profile and role. */
export async function me(signal?: AbortSignal): Promise<UserOut> {
  return (await http.get<UserOut>('/users/me', signal ? { signal } : {})).data;
}
