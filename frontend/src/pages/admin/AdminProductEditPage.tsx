// One product for an admin: edit (PUT, full replacement), deactivate (DELETE, logical) or
// reactivate (PUT is_active=true), and its inventory.
//
// PUT is "last write wins" in product-service. Before saving, the product is read again:
// if updated_at changed since the form was loaded, another admin modified it and the save
// is stopped until the current data is loaded. This check is preventive only: a change
// between that read and the PUT is still possible, because the service has no version check.
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { ApiError } from '../../api/errors';
import { deactivateProduct, getProduct, replaceProduct } from '../../api/products';
import type { ProductIn, ProductOut } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { isUncertain } from '../../admin/adminErrors';
import { InventoryPanel } from '../../admin/InventoryPanel';
import { ProductForm } from '../../admin/ProductForm';
import { fieldErrorsFromIssues, formFromProduct, matchesData, type FieldErrors, type ProductFormValues } from '../../admin/productRules';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { Spinner } from '../../components/Spinner';
import { StatusBadge } from '../../components/StatusBadge';
import { formatDateTime } from '../../utils/format';
import { NotFoundPage } from '../NotFoundPage';
import type { AdminProductNotice } from './AdminProductNewPage';

interface Notice {
  tone: 'success' | 'info' | 'warning' | 'error';
  text: string;
}

const NOTICES: Record<AdminProductNotice, Notice> = {
  created: { tone: 'success', text: 'Producto creado. Registra sus existencias en el inventario.' },
  recovered: { tone: 'success', text: 'El producto sí se había creado: no se volvió a enviar.' },
};

export function AdminProductEditPage() {
  const { productId } = useParams();
  const id = /^\d{1,10}$/.test(productId ?? '') ? Number(productId) : NaN;
  if (!Number.isInteger(id) || id < 1 || id > 2_147_483_647) return <NotFoundPage />;
  return <ProductEditor key={id} id={id} />;
}

type Load = { status: 'loading' } | { status: 'ready' } | { status: 'missing' } | { status: 'error'; error: unknown };

function ProductEditor({ id }: { id: number }) {
  const { user } = useAuth();
  const location = useLocation();
  const initialNotice = (location.state as { notice?: AdminProductNotice } | null)?.notice;
  const [load, setLoad] = useState<Load>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);
  /** Product as last read from the service: its updated_at is the reference for the check */
  const [loaded, setLoaded] = useState<ProductOut | null>(null);
  const [values, setValues] = useState<ProductFormValues | null>(null);
  const [serverErrors, setServerErrors] = useState<FieldErrors | undefined>();
  const [notice, setNotice] = useState<Notice | null>(initialNotice ? NOTICES[initialNotice] : null);
  const [failure, setFailure] = useState<{ title: string; error: unknown } | null>(null);
  const [conflict, setConflict] = useState<ProductOut | null>(null);
  const [busy, setBusy] = useState<'save' | 'status' | null>(null);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const busyRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoad({ status: 'loading' });
    getProduct(id, controller.signal)
      .then((product) => {
        if (controller.signal.aborted) return;
        setLoaded(product);
        setValues(formFromProduct(product));
        setLoad({ status: 'ready' });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoad(error instanceof ApiError && error.status === 404 ? { status: 'missing' } : { status: 'error', error });
      });
    return () => controller.abort();
  }, [id, attempt]);

  async function exclusive(kind: 'save' | 'status', task: () => Promise<void>) {
    if (busyRef.current) return; // one operation at a time; a double click sends one request
    busyRef.current = true;
    setBusy(kind);
    setFailure(null);
    setNotice(null);
    try {
      await task();
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }

  const save = (data: ProductIn) =>
    exclusive('save', async () => {
      if (!loaded) return;
      setServerErrors(undefined);
      let fresh: ProductOut;
      try {
        fresh = await getProduct(id);
      } catch (error) {
        setFailure({ title: 'No pudimos comprobar si el producto cambió; no se guardó nada.', error });
        return;
      }
      // Already holds these data (e.g. an earlier save whose answer was lost): nothing to send
      if (matchesData(fresh, data)) {
        setLoaded(fresh);
        setValues(formFromProduct(fresh));
        setNotice({ tone: 'success', text: 'Los datos guardados ya coinciden con el formulario.' });
        return;
      }
      if (fresh.updated_at !== loaded.updated_at) {
        setConflict(fresh);
        return;
      }
      try {
        const saved = await replaceProduct(id, { ...data, is_active: fresh.is_active });
        setLoaded(saved);
        setValues(formFromProduct(saved));
        setNotice({ tone: 'success', text: 'Cambios guardados.' });
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) setServerErrors({ sku: 'Ya existe otro producto con este SKU.' });
        else if (error instanceof ApiError && error.status === 422 && error.issues.length > 0) setServerErrors(fieldErrorsFromIssues(error.issues));
        else if (isUncertain(error)) {
          setNotice({ tone: 'warning', text: 'No sabemos si los cambios se guardaron. Vuelve a guardar: si ya se aplicaron, lo detectaremos sin repetirlos.' });
        } else setFailure({ title: 'No se guardaron los cambios.', error });
      }
    });

  /** Reads the product again after a status change, whatever the answer was */
  async function refreshAfter(action: string, error: unknown | null) {
    try {
      const fresh = await getProduct(id);
      setLoaded(fresh);
      if (error !== null && isUncertain(error)) {
        setNotice({ tone: 'info', text: `No recibimos la respuesta; el producto figura ahora como ${fresh.is_active ? 'activo' : 'inactivo'}.` });
      } else if (error !== null) {
        setFailure({ title: `No se pudo ${action} el producto.`, error });
      } else {
        setNotice({
          tone: 'success',
          text: fresh.is_active
            ? 'Producto reactivado: vuelve a aparecer en el catálogo. Su inventario no cambió.'
            : 'Producto desactivado: ya no aparece en el catálogo ni se puede agregar a carritos. Su inventario y los pedidos existentes se conservan.',
        });
      }
    } catch (readError) {
      setFailure({
        title: error === null ? `Se pidió ${action} el producto, pero no pudimos leer su estado actual.` : `No sabemos si se pudo ${action} el producto.`,
        error: error ?? readError,
      });
    }
  }

  const deactivate = () =>
    exclusive('status', async () => {
      setConfirmDeactivate(false);
      let error: unknown | null = null;
      try {
        await deactivateProduct(id);
      } catch (failureError) {
        error = failureError;
      }
      await refreshAfter('desactivar', error);
    });

  const reactivate = () =>
    exclusive('status', async () => {
      let error: unknown | null = null;
      try {
        // Current data, so that reactivating never brings back older values
        const fresh = await getProduct(id);
        const { sku, name, description, category, price } = fresh;
        await replaceProduct(id, { sku, name, description, category, price, is_active: true });
      } catch (failureError) {
        error = failureError;
      }
      await refreshAfter('reactivar', error);
    });

  if (load.status === 'loading') {
    return (
      <div className="catalog__state">
        <Spinner label="Cargando el producto…" />
      </div>
    );
  }
  if (load.status === 'missing') {
    return (
      <section className="not-found">
        <h1>Producto no encontrado</h1>
        <p>No existe un producto con el id {id}.</p>
        <Link to="/admin/productos" className="btn btn--secondary">
          Volver a productos
        </Link>
      </section>
    );
  }
  if (load.status === 'error' || !loaded || !values || !user) {
    return (
      <section className="catalog__state">
        <ErrorAlert error={load.status === 'error' ? load.error : null} title="No pudimos cargar el producto." />
        <Button variant="secondary" onClick={() => setAttempt((n) => n + 1)}>
          Reintentar
        </Button>
      </section>
    );
  }

  return (
    <section className="admin-product">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <Link to="/admin/productos">Productos</Link>
        <span aria-hidden="true">/</span>
        <span>{loaded.sku}</span>
      </nav>
      <header className="page-header page-header--row">
        <div>
          <h1>{loaded.name}</h1>
          <p className="page-header__lead">
            Id {loaded.id} · modificado el {formatDateTime(loaded.updated_at)}
          </p>
        </div>
        <StatusBadge tone={loaded.is_active ? 'success' : 'neutral'} label={loaded.is_active ? 'Activo' : 'Inactivo'} />
      </header>

      {notice && (
        <Alert tone={notice.tone}>
          <p>{notice.text}</p>
        </Alert>
      )}
      {failure && <ErrorAlert error={failure.error} title={failure.title} />}

      <div className="admin-product__layout">
        <div>
          <section className="card" aria-labelledby="product-data-title">
            <h2 className="card__title" id="product-data-title">
              Datos del producto
            </h2>
            {conflict && (
              <Alert tone="warning" title="Otro administrador modificó este producto mientras lo editabas.">
                <p>
                  Se modificó el {formatDateTime(conflict.updated_at)}. Para no sobrescribir esos cambios, carga los datos actuales antes de
                  guardar (perderás los cambios que no guardaste).
                </p>
                <Button
                  variant="secondary"
                  onClick={() => {
                    setLoaded(conflict);
                    setValues(formFromProduct(conflict));
                    setConflict(null);
                  }}
                >
                  Cargar datos actuales
                </Button>
              </Alert>
            )}
            <ProductForm
              idPrefix="edit-product"
              values={values}
              onChange={setValues}
              serverErrors={serverErrors}
              locked={conflict !== null || busy === 'status'}
              busy={busy === 'save'}
              submitLabel="Guardar cambios"
              onSubmit={(data) => void save(data)}
            />
          </section>

          <section className="card" aria-labelledby="product-status-title">
            <h2 className="card__title" id="product-status-title">
              Estado
            </h2>
            {loaded.is_active ? (
              <>
                <p>El producto está activo y visible en el catálogo.</p>
                {confirmDeactivate ? (
                  <div className="inline-confirm" role="group" aria-label="Confirmar desactivación">
                    <p>
                      Dejará de aparecer en el catálogo y no se podrá agregar a carritos. Su inventario y los pedidos existentes se conservan, y
                      podrás reactivarlo.
                    </p>
                    <div className="form-actions">
                      <Button variant="danger" loading={busy === 'status'} onClick={() => void deactivate()}>
                        Sí, desactivar
                      </Button>
                      <Button variant="ghost" disabled={busy !== null} onClick={() => setConfirmDeactivate(false)}>
                        Cancelar
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button variant="secondary" disabled={busy !== null} onClick={() => setConfirmDeactivate(true)}>
                    Desactivar producto
                  </Button>
                )}
              </>
            ) : (
              <>
                <p>El producto está inactivo: no aparece en el catálogo.</p>
                <Button variant="secondary" loading={busy === 'status'} disabled={busy === 'save'} onClick={() => void reactivate()}>
                  Reactivar producto
                </Button>
              </>
            )}
          </section>
        </div>
        <InventoryPanel key={user.id} productId={id} userId={user.id} />
      </div>
    </section>
  );
}
