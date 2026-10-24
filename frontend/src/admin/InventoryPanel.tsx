// Stock of one product for an admin: the record (available and reserved units), relative
// adjustments and their history. Four situations are kept apart: a record with units, a
// record with 0 units, no record at all (the first positive adjustment creates it) and
// inventory-service unavailable.
//
// An adjustment is saved in sessionStorage before it is sent and is repeated, if needed,
// with the SAME key and body. While an attempt is unresolved no other adjustment of this
// product can be started, so the two can never be confused.
import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { adjustStock, getStockRecord, listAdjustments } from '../api/inventory';
import type { AdjustmentOut, StockAdmin } from '../api/types';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { FormField } from '../components/FormField';
import { Spinner } from '../components/Spinner';
import { newKey } from '../orders/pendingOps';
import { formatDateTime } from '../utils/format';
import { classifyAdjustmentError, isNoStockRecord, type AdjustmentProblem } from './adminErrors';
import { clearPendingAdjustment, getPendingAdjustment, savePendingAdjustment, type PendingAdjustment } from './pendingAdjustments';

export const HISTORY_PAGE_SIZE = 20;
export const MAX_UNITS = 100_000;
export const REASON_MAX = 200;

type StockState =
  | { status: 'loading' }
  | { status: 'record'; stock: StockAdmin }
  | { status: 'none' }
  | { status: 'error'; error: unknown };

type HistoryState =
  | { status: 'loading' }
  | { status: 'ready'; items: AdjustmentOut[]; offset: number }
  | { status: 'error'; error: unknown };

interface Notice {
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

function describe(delta: number): string {
  const units = Math.abs(delta);
  return `${delta > 0 ? 'Agregar' : 'Quitar'} ${units} ${units === 1 ? 'unidad' : 'unidades'}`;
}

export function InventoryPanel({ productId, userId }: { productId: number; userId: number }) {
  const [stock, setStock] = useState<StockState>({ status: 'loading' });
  const [history, setHistory] = useState<HistoryState>({ status: 'loading' });
  const [pending, setPending] = useState<PendingAdjustment | null>(() => getPendingAdjustment(userId, productId));
  const [problem, setProblem] = useState<AdjustmentProblem | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [mode, setMode] = useState<'add' | 'remove'>('add');
  const [units, setUnits] = useState('');
  const [reason, setReason] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const busyRef = useRef(false);
  const restored = useRef(pending !== null);

  const loadStock = useCallback(async (signal?: AbortSignal) => {
    try {
      const record = await getStockRecord(productId, signal);
      if (!signal?.aborted) setStock({ status: 'record', stock: record });
    } catch (error) {
      if (signal?.aborted) return;
      setStock(isNoStockRecord(error) ? { status: 'none' } : { status: 'error', error });
    }
  }, [productId]);

  const loadHistory = useCallback(async (offset: number, signal?: AbortSignal) => {
    setHistory({ status: 'loading' });
    try {
      const items = await listAdjustments(productId, HISTORY_PAGE_SIZE, offset, signal);
      if (!signal?.aborted) setHistory({ status: 'ready', items, offset });
    } catch (error) {
      if (!signal?.aborted) setHistory({ status: 'error', error });
    }
  }, [productId]);

  useEffect(() => {
    const controller = new AbortController();
    void loadStock(controller.signal);
    void loadHistory(0, controller.signal);
    return () => controller.abort();
  }, [loadStock, loadHistory]);

  /** Sends (or repeats) exactly the saved attempt */
  async function send(record: PendingAdjustment) {
    if (busyRef.current) return; // a double click sends one request
    busyRef.current = true;
    setBusy(true);
    setProblem(null);
    setNotice(null);
    setConfirmDiscard(false);
    try {
      const { adjustment, status } = await adjustStock(productId, { delta: record.delta, reason: record.reason }, record.key);
      clearPendingAdjustment(userId, productId, record.key);
      setPending(null);
      restored.current = false;
      const change = `${adjustment.available_before} → ${adjustment.available_after} disponibles`;
      setNotice(
        status === 200
          ? { tone: 'info', text: `Este ajuste ya estaba aplicado (${change}); no se sumó de nuevo.` }
          : { tone: 'success', text: `Ajuste aplicado: ${describe(adjustment.delta).toLowerCase()} (${change}).` },
      );
      setUnits('');
      setReason('');
      void loadStock();
      void loadHistory(0);
    } catch (error) {
      const found = classifyAdjustmentError(error);
      if (found.kind === 'not-applied') {
        clearPendingAdjustment(userId, productId, record.key);
        setPending(null);
        restored.current = false;
        void loadStock();
      }
      setProblem(found);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || busyRef.current) return;
    const text = units.trim();
    const count = /^\d{1,6}$/.test(text) ? Number(text) : NaN;
    if (!Number.isInteger(count) || count < 1 || count > MAX_UNITS) {
      setFormError(`Ingresa un número entero de unidades entre 1 y ${MAX_UNITS.toLocaleString('es-EC')}.`);
      return;
    }
    const cleanReason = reason.trim();
    if (cleanReason.length > REASON_MAX) {
      setFormError(`El motivo admite hasta ${REASON_MAX} caracteres.`);
      return;
    }
    setFormError(null);
    // Saved BEFORE sending: a timeout or a reload repeats exactly this request
    const record = savePendingAdjustment({
      userId, productId, delta: mode === 'add' ? count : -count, reason: cleanReason, key: newKey(),
    });
    setPending(record);
    void send(record);
  }

  function discard() {
    if (!pending) return;
    clearPendingAdjustment(userId, productId, pending.key);
    setPending(null);
    setProblem(null);
    setConfirmDiscard(false);
    restored.current = false;
    setNotice({ tone: 'info', text: 'Intento descartado. Revisa el historial para confirmar las existencias actuales.' });
  }

  const canDiscard = problem?.kind === 'key-conflict' || problem?.kind === 'uncertain' || (restored.current && !busy);

  return (
    <section className="card inventory-panel" aria-labelledby="inventory-title">
      <h2 className="card__title" id="inventory-title">
        Inventario
      </h2>

      {stock.status === 'loading' && <Spinner label="Cargando existencias…" />}
      {stock.status === 'record' && (
        <dl className="inventory-panel__figures">
          <div>
            <dt>Disponibles</dt>
            <dd>{stock.stock.available}</dd>
          </div>
          <div>
            <dt>Reservadas</dt>
            <dd>{stock.stock.reserved}</dd>
          </div>
          <div>
            <dt>Actualizado</dt>
            <dd>{formatDateTime(stock.stock.updated_at)}</dd>
          </div>
        </dl>
      )}
      {stock.status === 'record' && stock.stock.available === 0 && (
        <p className="inventory-panel__note">Registro con 0 unidades disponibles: el producto figura como agotado.</p>
      )}
      {stock.status === 'none' && (
        <Alert tone="info" title="Sin registro de inventario">
          <p>Este producto todavía no tiene existencias registradas. El primer ajuste positivo crea el registro.</p>
        </Alert>
      )}
      {stock.status === 'error' && (
        <div>
          <ErrorAlert error={stock.error} title="Inventario no disponible en este momento." />
          <Button variant="secondary" onClick={() => void loadStock()}>
            Reintentar
          </Button>
        </div>
      )}

      {notice && (
        <Alert tone={notice.tone}>
          <p>{notice.text}</p>
        </Alert>
      )}

      {pending ? (
        <Alert tone={problem?.kind === 'key-conflict' ? 'error' : 'warning'} title="Ajuste sin confirmar">
          <p>
            {describe(pending.delta)}
            {pending.reason ? ` · motivo «${pending.reason}»` : ''}.{' '}
            {problem
              ? problem.message
              : busy
                ? 'Enviando…'
                : 'Quedó guardado en esta pestaña sin conocer su resultado. Reintenta con la misma clave para saber si se aplicó.'}
          </p>
          <div className="form-actions">
            {problem?.kind !== 'key-conflict' && (
              <Button variant="secondary" loading={busy} onClick={() => void send(pending)}>
                Reintentar con la misma clave
              </Button>
            )}
            {canDiscard && !confirmDiscard && (
              <Button variant="ghost" disabled={busy} onClick={() => setConfirmDiscard(true)}>
                Descartar este intento…
              </Button>
            )}
          </div>
          {confirmDiscard && (
            <div className="inline-confirm" role="group" aria-label="Confirmar descarte">
              <p>Descarta solo si revisaste el historial y sabes si el ajuste se aplicó; si no, podrías repetirlo.</p>
              <div className="form-actions">
                <Button variant="danger" onClick={discard}>
                  Sí, descartar
                </Button>
                <Button variant="ghost" onClick={() => setConfirmDiscard(false)}>
                  Volver
                </Button>
              </div>
            </div>
          )}
        </Alert>
      ) : (
        <>
          {problem && (
            <Alert tone={problem.kind === 'not-applied' ? 'error' : 'warning'}>
              <p>{problem.message}</p>
            </Alert>
          )}
          <form className="adjust-form" onSubmit={submit} noValidate>
            <fieldset className="adjust-form__mode" disabled={busy}>
              <legend className="field__label">Ajuste</legend>
              <label>
                <input type="radio" name="adjust-mode" checked={mode === 'add'} onChange={() => setMode('add')} /> Agregar unidades
              </label>
              <label>
                <input type="radio" name="adjust-mode" checked={mode === 'remove'} onChange={() => setMode('remove')} /> Quitar unidades
              </label>
            </fieldset>
            <FormField
              id="adjust-units"
              label="Unidades"
              inputMode="numeric"
              autoComplete="off"
              value={units}
              disabled={busy}
              error={formError ?? undefined}
              hint="Se suman o restan a las existencias actuales en el servidor."
              onChange={(e) => setUnits(e.target.value)}
            />
            <FormField
              id="adjust-reason"
              label="Motivo (opcional)"
              value={reason}
              maxLength={REASON_MAX}
              disabled={busy}
              onChange={(e) => setReason(e.target.value)}
            />
            <div className="form-actions">
              <Button type="submit" loading={busy}>
                Aplicar ajuste
              </Button>
            </div>
          </form>
        </>
      )}

      <h3 className="inventory-panel__subtitle">Historial de ajustes</h3>
      {history.status === 'loading' && <Spinner label="Cargando historial…" />}
      {history.status === 'error' && (
        <div>
          <ErrorAlert error={history.error} title="No pudimos cargar el historial." />
          <Button variant="secondary" onClick={() => void loadHistory(0)}>
            Reintentar
          </Button>
        </div>
      )}
      {history.status === 'ready' && history.items.length === 0 && (
        <p className="inventory-panel__note">{history.offset === 0 ? 'Todavía no hay ajustes.' : 'No hay más ajustes.'}</p>
      )}
      {history.status === 'ready' && history.items.length > 0 && (
        <ul className="adjust-history" aria-label="Historial de ajustes">
          {history.items.map((item) => (
            <li key={item.id}>
              <p className="adjust-history__change">
                <strong className={item.delta > 0 ? 'adjust-history__up' : 'adjust-history__down'}>
                  {item.delta > 0 ? `+${item.delta}` : item.delta}
                </strong>{' '}
                <span>
                  {item.available_before} → {item.available_after} disponibles
                </span>
              </p>
              <p className="adjust-history__meta">
                {formatDateTime(item.created_at)}
                {item.reason ? ` · ${item.reason}` : ''}
              </p>
            </li>
          ))}
        </ul>
      )}
      {history.status === 'ready' && (history.offset > 0 || history.items.length === HISTORY_PAGE_SIZE) && (
        <nav className="pagination" aria-label="Paginación del historial">
          <Button variant="secondary" disabled={history.offset === 0} onClick={() => void loadHistory(Math.max(0, history.offset - HISTORY_PAGE_SIZE))}>
            Anterior
          </Button>
          <Button
            variant="secondary"
            disabled={history.items.length < HISTORY_PAGE_SIZE}
            onClick={() => void loadHistory(history.offset + HISTORY_PAGE_SIZE)}
          >
            Siguiente
          </Button>
        </nav>
      )}
    </section>
  );
}
