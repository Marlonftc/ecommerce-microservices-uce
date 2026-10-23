// Diagnosis data of a pending operation (payload and last_error) shown to an admin.
// order-service stores business data only, but the view is defensive anyway: fields whose
// name looks like a secret are hidden, token-like text is masked, and the length is limited.
// The result is plain text: React escapes it, it is never inserted as HTML.

export const PAYLOAD_MAX_CHARS = 4000;
export const ERROR_MAX_CHARS = 2000;
export const HIDDEN = '[oculto]';

const SECRET_NAME = /token|secret|password|passwd|authorization|api[-_]?key|internal[-_]?key|credential|cookie/i;
const SECRET_TEXT: [RegExp, string][] = [
  [/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, `Bearer ${HIDDEN}`],
  [/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/g, HIDDEN], // JWT
  [/((?:x-internal-key|api[-_]?key|password|secret|token)\s*[=:]\s*)("[^"]*"|'[^']*'|\S+)/gi, `$1${HIDDEN}`],
];

function redactValue(value: unknown, depth = 0): unknown {
  if (depth > 6) return '…';
  if (Array.isArray(value)) return value.map((item) => redactValue(item, depth + 1));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [key, SECRET_NAME.test(key) ? HIDDEN : redactValue(item, depth + 1)]),
    );
  }
  return typeof value === 'string' ? redactText(value) : value;
}

export function redactText(text: string): string {
  return SECRET_TEXT.reduce((result, [pattern, replacement]) => result.replace(pattern, replacement), text);
}

export function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}… (${text.length - max} caracteres más)` : text;
}

/** Payload as indented JSON text, without secret-looking fields, at most PAYLOAD_MAX_CHARS */
export function payloadText(payload: unknown): string {
  let text: string;
  try {
    text = JSON.stringify(redactValue(payload), null, 2) ?? '';
  } catch {
    text = '(no se pudo mostrar)';
  }
  return truncate(text, PAYLOAD_MAX_CHARS);
}

export function errorText(error: string | null): string {
  return error ? truncate(redactText(error), ERROR_MAX_CHARS) : '';
}
