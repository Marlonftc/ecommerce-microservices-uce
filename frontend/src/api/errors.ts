// Every failure of an API call becomes an ApiError with a message in Spanish that can
// be shown to the user. The backend answers errors in three JSON shapes:
//   {"detail": "Product not found"}                           FastAPI, text
//   {"detail": [{"loc": [...], "msg": "...", ...}]}           FastAPI validation (422)
//   {"detail": {"code": "CART_LOCKED", "message": "..."}}     order-service and the gateway

export type ApiErrorKind = 'http' | 'network' | 'timeout';

export interface ValidationIssue {
  field: string;
  message: string;
}

const MESSAGES_BY_CODE: Record<string, string> = {
  CART_LOCKED: 'Hay una compra en curso: el carrito no se puede modificar hasta que termine.',
  CART_CHANGED: 'El carrito cambió desde la última vez que se cargó. Revísalo e inténtalo de nuevo.',
  CART_FULL: 'El carrito ya tiene el máximo de productos distintos permitido.',
  CART_EMPTY: 'El carrito está vacío.',
  PRODUCT_NOT_AVAILABLE: 'El producto no está disponible.',
  INSUFFICIENT_STOCK: 'No hay existencias suficientes para completar el pedido.',
  IDEMPOTENCY_KEY_REUSED: 'Esta operación ya se registró con datos distintos.',
  ORDER_NOT_PAYABLE: 'El pedido ya no se puede pagar.',
  ORDER_NOT_CANCELLABLE: 'El pedido ya no se puede cancelar.',
  OPERATION_NOT_RETRYABLE: 'Solo se puede reintentar una operación fallida que no esté en proceso.',
  NOT_FOUND: 'La ruta solicitada no existe.',
  PAYLOAD_TOO_LARGE: 'La solicitud es demasiado grande.',
  TOO_MANY_REQUESTS: 'Demasiados intentos. Espera unos segundos e inténtalo de nuevo.',
  UPSTREAM_UNAVAILABLE: 'El servicio no está disponible en este momento. Inténtalo más tarde.',
  UPSTREAM_TIMEOUT: 'El servicio tardó demasiado en responder. Inténtalo más tarde.',
};

const MESSAGES_BY_STATUS: Record<number, string> = {
  400: 'La solicitud no es válida.',
  401: 'Tu sesión no es válida o ha expirado.',
  403: 'No tienes permiso para realizar esta acción.',
  404: 'No se encontró el recurso solicitado.',
  409: 'La operación no se puede completar en el estado actual.',
  413: 'La solicitud es demasiado grande.',
  422: 'Revisa los datos ingresados.',
  429: 'Demasiados intentos. Espera unos segundos e inténtalo de nuevo.',
  500: 'Ocurrió un error interno en el servicio.',
  502: 'El servicio no está disponible en este momento. Inténtalo más tarde.',
  503: 'El servicio no está disponible en este momento. Inténtalo más tarde.',
  504: 'El servicio tardó demasiado en responder. Inténtalo más tarde.',
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  /** HTTP status, or 0 when no response arrived (network error or timeout) */
  readonly status: number;
  /** Business or gateway code, e.g. CART_LOCKED or UPSTREAM_TIMEOUT */
  readonly code: string | null;
  /** Original text sent by the backend (English), for diagnosis only */
  readonly detail: string | null;
  readonly issues: ValidationIssue[];
  /** X-Request-ID of the response: lets the request be found in the gateway log */
  readonly requestId: string | null;
  /** Seconds from the Retry-After header (429) */
  readonly retryAfter: number | null;

  constructor(init: {
    kind: ApiErrorKind;
    message: string;
    status?: number;
    code?: string | null;
    detail?: string | null;
    issues?: ValidationIssue[];
    requestId?: string | null;
    retryAfter?: number | null;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.kind = init.kind;
    this.status = init.status ?? 0;
    this.code = init.code ?? null;
    this.detail = init.detail ?? null;
    this.issues = init.issues ?? [];
    this.requestId = init.requestId ?? null;
    this.retryAfter = init.retryAfter ?? null;
  }

  static network(): ApiError {
    return new ApiError({
      kind: 'network',
      message: 'No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.',
    });
  }

  static timeout(): ApiError {
    return new ApiError({
      kind: 'timeout',
      message: 'El servidor no respondió a tiempo. La operación pudo haberse completado; revisa su estado antes de repetirla.',
    });
  }

  /** Builds the error from a non-2xx response; body is the parsed JSON, a text or null */
  static fromResponse(status: number, body: unknown, requestId: string | null, retryAfter: string | null): ApiError {
    const { code, detail, issues } = readDetail(body);
    const message = (code && MESSAGES_BY_CODE[code]) ?? MESSAGES_BY_STATUS[status] ?? `Error inesperado (HTTP ${status}).`;
    const seconds = retryAfter !== null && /^\d+$/.test(retryAfter.trim()) ? Number(retryAfter) : null;
    return new ApiError({ kind: 'http', message, status, code, detail, issues, requestId, retryAfter: seconds });
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readDetail(body: unknown): { code: string | null; detail: string | null; issues: ValidationIssue[] } {
  if (!isRecord(body)) {
    return { code: null, detail: typeof body === 'string' && body ? body.slice(0, 300) : null, issues: [] };
  }
  const detail = body.detail;
  if (typeof detail === 'string') {
    return { code: null, detail, issues: [] };
  }
  if (Array.isArray(detail)) {
    const issues = detail.filter(isRecord).map((item) => ({
      field: Array.isArray(item.loc) ? item.loc.filter((p) => p !== 'body' && p !== 'query').join('.') : '',
      message: typeof item.msg === 'string' ? item.msg : 'Valor no válido',
    }));
    return { code: null, detail: null, issues };
  }
  if (isRecord(detail)) {
    return {
      code: typeof detail.code === 'string' ? detail.code : null,
      detail: typeof detail.message === 'string' ? detail.message : null,
      issues: [],
    };
  }
  return { code: null, detail: null, issues: [] };
}
