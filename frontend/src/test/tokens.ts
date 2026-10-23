// Test helper: builds an unsigned JWT-like token with the given claims.
// The app never verifies signatures (the services do), so this is enough for tests.
function base64Url(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString('base64url');
}

export function makeToken(claims: { sub?: string; role?: string; exp?: number }, label = 'sig'): string {
  const payload = { sub: '7', role: 'customer', exp: Math.floor(Date.now() / 1000) + 1800, ...claims };
  return `${base64Url({ alg: 'RS256', typ: 'JWT' })}.${base64Url(payload)}.${label}`;
}
