// Pending operations of the saga (order-service), for an admin: filters in the URL, 20 per
// page with the total, diagnosis data and the manual retry of FAILED operations.
//
// order-service has the last word: it only reactivates an operation that is still FAILED and
// that no runner holds (409 OPERATION_NOT_RETRYABLE otherwise). An unknown outcome is never
// repeated: the operations of that order are read again and their real state is shown.
// Every manual attempt also reloads the filtered page and its total with GET only.
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { listOperations, retryOperation, type OperationFilters } from '../../api/adminOperations';
import { ApiError } from '../../api/errors';
import { isOrderId } from '../../api/orders';
import type { NumberedPage, OperationKind, OperationOut, OperationStatus, RetryOut } from '../../api/types';
import { useForbiddenCheck } from '../../admin/adminSession';
import { isUncertain } from '../../admin/adminErrors';
import { errorText, payloadText } from '../../admin/diagnostics';
import { readCatalogParams } from '../../catalog/params';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Pagination } from '../../components/Pagination';
import { Spinner } from '../../components/Spinner';
import { StatusBadge, type BadgeTone } from '../../components/StatusBadge';
import { shortId } from '../../orders/orderStatus';
import { formatDateTime } from '../../utils/format';

export const OPERATIONS_PAGE_SIZE = 20;

export const KIND_LABELS: Record<OperationKind, string> = {
  RESOLVE_PAYMENT: 'Resolver pago',
  CONFIRM_INVENTORY: 'Confirmar inventario',
  RELEASE_INVENTORY: 'Liberar inventario',
  SEND_NOTIFICATION: 'Enviar notificación',
};
const STATUS_VIEW: Record<OperationStatus, { label: string; tone: BadgeTone }> = {
  PENDING: { label: 'Pendiente', tone: 'info' },
  DONE: { label: 'Completada', tone: 'success' },
  FAILED: { label: 'Fallida', tone: 'danger' },
};
const ERROR_LABELS = { TRANSIENT: 'Temporal', PERMANENT: 'Permanente' } as const;

export const OUTCOME_TEXT: Record<RetryOut['outcome'], string> = {
  DONE: 'Reintento ejecutado: la operación se completó.',
  RETRY: 'Reintento ejecutado: volvió a fallar de forma temporal; el worker la intentará de nuevo automáticamente.',
  FAILED: 'Reintento ejecutado: volvió a fallar. Revisa el último error.',
  SKIPPED: 'Se reactivó, pero no se ejecutó ahora: otro proceso la tomó o ya no estaba pendiente.',
  LOST: 'Otro proceso tomó la operación mientras se ejecutaba. Revisa su estado actual.',
};

const STATUSES: OperationStatus[] = ['PENDING', 'DONE', 'FAILED'];
const KINDS = Object.keys(KIND_LABELS) as OperationKind[];

function readFilters(search: URLSearchParams): { filters: OperationFilters; invalidOrder: string | null } {
  const status = search.get('estado');
  const kind = search.get('tipo');
  const errorKind = search.get('error');
  const order = (search.get('pedido') ?? '').trim();
  return {
    filters: {
      status: STATUSES.includes(status as OperationStatus) ? (status as OperationStatus) : undefined,
      kind: KINDS.includes(kind as OperationKind) ? (kind as OperationKind) : undefined,
      errorKind: errorKind === 'TRANSIENT' || errorKind === 'PERMANENT' ? errorKind : undefined,
      orderId: isOrderId(order) ? order : undefined,
    },
    invalidOrder: order && !isOrderId(order) ? order : null,
  };
}

function toSearch(values: { estado: string; tipo: string; error: string; pedido: string }, page = 1): URLSearchParams {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) if (value.trim()) search.set(key, value.trim());
  if (page > 1) search.set('pagina', String(page));
  return search;
}

function readDraft(search: URLSearchParams) {
  const { filters, invalidOrder } = readFilters(search);
  return {
    estado: filters.status ?? '', tipo: filters.kind ?? '', error: filters.errorKind ?? '',
    pedido: invalidOrder ?? filters.orderId ?? '',
  };
}

function withPage(search: URLSearchParams, page: number): URLSearchParams {
  const next = new URLSearchParams(search);
  if (page > 1) next.set('pagina', String(page));
  else next.delete('pagina');
  return next;
}

const isLocked = (op: OperationOut, now: number) => op.locked_until !== null && Date.parse(op.locked_until) > now;

type Result = { status: 'loading' } | { status: 'ready'; page: NumberedPage<OperationOut> } | { status: 'error'; error: unknown };

interface Notice {
  opId: number;
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

export function AdminOperationsPage() {
  const onForbidden = useForbiddenCheck();
  const [search, setSearch] = useSearchParams();
  const searchKey = search.toString();
  const { page } = readCatalogParams(search);
  const { filters, invalidOrder } = readFilters(search);
  const filterKey = JSON.stringify(filters);
  const [draft, setDraft] = useState(() => readDraft(search));
  const [draftError, setDraftError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<Result>({ status: 'loading' });
  const [notice, setNotice] = useState<Notice | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const busyRef = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    setDraft(readDraft(new URLSearchParams(searchKey)));
    setDraftError(null);
    setConfirmId(null);
  }, [searchKey]);

  useEffect(() => {
    if (invalidOrder) return undefined;
    const controller = new AbortController();
    setResult({ status: 'loading' });
    listOperations(JSON.parse(filterKey) as OperationFilters, page, OPERATIONS_PAGE_SIZE, controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        const lastPage = Math.max(1, Math.ceil(data.total / OPERATIONS_PAGE_SIZE));
        if (page > lastPage) {
          // Replace the invalid history entry; retain every active filter.
          setSearch(withPage(new URLSearchParams(searchKey), lastPage), { replace: true });
          return;
        }
        setNow(Date.now());
        setResult({ status: 'ready', page: data });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (!onForbidden(error)) setResult({ status: 'error', error });
      });
    return () => controller.abort();
  }, [filterKey, invalidOrder, page, attempt, onForbidden, searchKey, setSearch]);

  // When the nearest lock expires, render again so that the retry becomes available
  const items = result.status === 'ready' ? result.page.items : [];
  const nextUnlock = Math.min(...items.filter((op) => isLocked(op, now)).map((op) => Date.parse(op.locked_until!)));
  useEffect(() => {
    if (!Number.isFinite(nextUnlock)) return undefined;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, nextUnlock - Date.now()) + 50);
    return () => clearTimeout(timer);
  }, [nextUnlock]);

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    if (draft.pedido.trim() && !isOrderId(draft.pedido.trim())) {
      setDraftError('El id del pedido debe ser un UUID completo, por ejemplo 0b9a6c1e-4f2d-4c8a-9e3b-7d5f1a2b3c4d.');
      return;
    }
    setDraftError(null);
    setNotice(null);
    const next = toSearch(draft);
    if (next.toString() === searchKey) setAttempt((n) => n + 1);
    else setSearch(next);
  }

  /** Reads the operations of the order again after an unknown outcome; never POSTs again */
  async function readBack(op: OperationOut): Promise<OperationOut | null> {
    const page = await listOperations({ orderId: op.order_id }, 1, 100);
    return page.items.find((item) => item.id === op.id) ?? null;
  }

  async function retry(op: OperationOut) {
    if (busyRef.current) return; // a double click sends one retry
    busyRef.current = true;
    setBusyId(op.id);
    setConfirmId(null);
    setNotice(null);
    let refreshList = true;
    try {
      const { outcome } = await retryOperation(op.id);
      if (!mounted.current) return;
      setNotice({ opId: op.id, tone: outcome === 'DONE' ? 'success' : outcome === 'RETRY' || outcome === 'SKIPPED' ? 'info' : 'warning', text: OUTCOME_TEXT[outcome] });
    } catch (error) {
      if (!mounted.current) return;
      if (onForbidden(error)) {
        refreshList = false;
        return;
      }
      if (error instanceof ApiError && error.status === 409 && error.code === 'OPERATION_NOT_RETRYABLE') {
        const status = typeof error.extra.status === 'string' ? error.extra.status : '';
        const claimed = error.extra.claimed === true;
        const label = STATUS_VIEW[status as OperationStatus]?.label.toLowerCase() ?? status;
        setNotice({
          opId: op.id,
          tone: 'info',
          text: `order-service no la reintentó: ${claimed ? 'otro proceso la está ejecutando ahora' : `ya no está fallida (estado: ${label})`}. La lista se actualizó.`,
        });
      } else if (error instanceof ApiError && error.status === 404) {
        setNotice({ opId: op.id, tone: 'error', text: 'La operación ya no existe. La lista se actualizó.' });
      } else if (isUncertain(error)) {
        try {
          const current = await readBack(op);
          if (!mounted.current) return;
          setNotice({
            opId: op.id,
            tone: 'warning',
            text: current
              ? `No recibimos la respuesta del reintento; pudo ejecutarse. Estado actual: ${STATUS_VIEW[current.status].label.toLowerCase()}, ${current.attempts} ${current.attempts === 1 ? 'intento' : 'intentos'}. No se volvió a enviar.`
              : 'No recibimos la respuesta del reintento y no encontramos la operación al consultar de nuevo.',
          });
        } catch (readError) {
          if (!mounted.current) return;
          if (onForbidden(readError)) {
            refreshList = false;
            return;
          }
          setNotice({ opId: op.id, tone: 'warning', text: 'No recibimos la respuesta del reintento y no pudimos consultar su estado. Actualiza la lista más tarde; no se volverá a enviar solo.' });
        }
      } else {
        setNotice({ opId: op.id, tone: 'error', text: error instanceof ApiError ? error.message : 'No se pudo reintentar.' });
      }
    } finally {
      busyRef.current = false;
      if (mounted.current) {
        setBusyId(null);
        // Refresh the current URL, even if navigation changed during the POST.
        if (refreshList) setAttempt((n) => n + 1);
      }
    }
  }

  const select = (key: 'estado' | 'tipo' | 'error', label: string, options: [string, string][]) => (
    <div className="field">
      <label htmlFor={`op-${key}`} className="field__label">
        {label}
      </label>
      <select id={`op-${key}`} className="field__input" value={draft[key]} onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}>
        <option value="">Todos</option>
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );

  return (
    <section className="admin-operations">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <span>Operaciones pendientes</span>
      </nav>
      <header className="page-header">
        <h1>Operaciones pendientes</h1>
        <p className="page-header__lead">
          Pasos de las compras que order-service guarda y el worker repite hasta completarlos: pagos, inventario y notificaciones.
        </p>
      </header>

      <form className="operations-filters" onSubmit={applyFilters} noValidate>
        {select('estado', 'Estado', STATUSES.map((s) => [s, STATUS_VIEW[s].label]))}
        {select('tipo', 'Tipo', KINDS.map((k) => [k, KIND_LABELS[k]]))}
        {select('error', 'Tipo de error', [['TRANSIENT', 'Temporal'], ['PERMANENT', 'Permanente']])}
        <div className={`field${draftError ? ' field--invalid' : ''}`}>
          <label htmlFor="op-pedido" className="field__label">
            Id del pedido
          </label>
          <input
            id="op-pedido"
            className="field__input"
            value={draft.pedido}
            autoComplete="off"
            aria-invalid={draftError ? true : undefined}
            onChange={(e) => setDraft((d) => ({ ...d, pedido: e.target.value }))}
          />
          {draftError && <p className="field__error">{draftError}</p>}
        </div>
        <div className="operations-filters__actions">
          <Button type="submit">Filtrar</Button>
        </div>
      </form>

      {notice && (
        <Alert tone={notice.tone} title={`Operación #${notice.opId}`}>
          <p>{notice.text}</p>
        </Alert>
      )}

      {invalidOrder && (
        <Alert tone="error">
          <p>El id de pedido de la dirección no es válido; no se hizo la consulta.</p>
        </Alert>
      )}
      {!invalidOrder && result.status === 'loading' && (
        <div className="catalog__state">
          <Spinner label="Cargando operaciones…" />
        </div>
      )}
      {!invalidOrder && result.status === 'error' && (
        <div className="catalog__state">
          <ErrorAlert error={result.error} title="No pudimos cargar las operaciones." />
          <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
            Reintentar
          </Button>
        </div>
      )}
      {!invalidOrder && result.status === 'ready' && result.page.total === 0 && <EmptyState title="No hay operaciones con estos filtros" />}
      {!invalidOrder && result.status === 'ready' && result.page.total > 0 && items.length === 0 && (
        <EmptyState title="Esta página no tiene operaciones" action={
          <Link to={`/admin/operaciones?${withPage(search, 1)}`} className="btn btn--secondary">Ir a la primera página</Link>
        } />
      )}
      {!invalidOrder && result.status === 'ready' && items.length > 0 && (
        <>
          <p className="catalog__summary">{result.page.total} operaciones</p>
          <ul className="operation-list" aria-label="Operaciones">
            {items.map((op) => {
              const locked = isLocked(op, now);
              const canRetry = op.status === 'FAILED' && !locked;
              const view = STATUS_VIEW[op.status];
              return (
                <li key={op.id} className="operation">
                  <div className="operation__head">
                    <span className="operation__kind">{KIND_LABELS[op.kind] ?? op.kind}</span>
                    <StatusBadge tone={view.tone} label={view.label} />
                    {op.error_kind && <StatusBadge tone={op.error_kind === 'PERMANENT' ? 'danger' : 'warning'} label={`Error ${ERROR_LABELS[op.error_kind].toLowerCase()}`} />}
                    {locked && <StatusBadge tone="info" label={`En proceso hasta ${formatDateTime(op.locked_until)}`} />}
                  </div>
                  <p className="operation__meta">
                    #{op.id} · pedido <Link to={`/pedidos/${op.order_id}`}>{shortId(op.order_id)}</Link> · {op.attempts}{' '}
                    {op.attempts === 1 ? 'intento' : 'intentos'} · actualizada {formatDateTime(op.updated_at)}
                    {op.status !== 'DONE' && <> · próximo intento {formatDateTime(op.next_attempt_at)}</>}
                    {op.completed_at && <> · completada {formatDateTime(op.completed_at)}</>}
                  </p>
                  <details className="operation__details">
                    <summary>Diagnóstico</summary>
                    <p className="operation__label">Último error</p>
                    <pre className="operation__pre">{errorText(op.last_error) || 'Sin error registrado.'}</pre>
                    <p className="operation__label">Datos de la operación</p>
                    <pre className="operation__pre">{payloadText(op.payload)}</pre>
                  </details>
                  {canRetry && confirmId !== op.id && (
                    <div className="form-actions">
                      <Button
                        variant="secondary"
                        loading={busyId === op.id}
                        disabled={busyId !== null && busyId !== op.id}
                        onClick={() => (op.error_kind === 'PERMANENT' ? setConfirmId(op.id) : void retry(op))}
                      >
                        Reintentar
                      </Button>
                    </div>
                  )}
                  {canRetry && confirmId === op.id && (
                    <div className="inline-confirm" role="group" aria-label="Confirmar reintento">
                      <p>
                        Es un error permanente (de contrato o de configuración). Reintentarla sin corregir la causa volverá a fallar. ¿Reintentar de
                        todos modos?
                      </p>
                      <div className="form-actions">
                        <Button variant="danger" onClick={() => void retry(op)}>
                          Sí, reintentar
                        </Button>
                        <Button variant="ghost" onClick={() => setConfirmId(null)}>
                          Cancelar
                        </Button>
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <Pagination
            page={page}
            totalPages={Math.max(1, Math.ceil(result.page.total / OPERATIONS_PAGE_SIZE))}
            hrefFor={(target) => {
              const query = withPage(search, target).toString();
              return query ? `/admin/operaciones?${query}` : '/admin/operaciones';
            }}
          />
        </>
      )}
    </section>
  );
}
