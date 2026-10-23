// New product. POST /products has no Idempotency-Key, so an unknown outcome is never
// repeated automatically: first the products are searched for the sent SKU (see
// createRecovery). The SKU is unique in product-service, which is what guarantees that a
// controlled retry can never create a duplicate.
import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../../api/errors';
import { createProduct } from '../../api/products';
import type { ProductIn, ProductOut } from '../../api/types';
import { isUncertain } from '../../admin/adminErrors';
import { findCreatedProduct, type CreateCheck } from '../../admin/createRecovery';
import { ProductForm } from '../../admin/ProductForm';
import { EMPTY_FORM, fieldErrorsFromIssues, type FieldErrors } from '../../admin/productRules';
import { Alert, ErrorAlert } from '../../components/Alert';
import { Button } from '../../components/Button';
import { Spinner } from '../../components/Spinner';

export type AdminProductNotice = 'created' | 'recovered';

type Phase =
  | { name: 'editing' }
  | { name: 'sending'; data: ProductIn }
  | { name: 'checking'; data: ProductIn }
  | { name: 'unknown'; data: ProductIn; check: Extract<CreateCheck, { status: 'unknown' }> }
  | { name: 'absent'; data: ProductIn }
  | { name: 'different'; data: ProductIn; product: ProductOut };

export function AdminProductNewPage() {
  const navigate = useNavigate();
  const [values, setValues] = useState(EMPTY_FORM);
  const [phase, setPhase] = useState<Phase>({ name: 'editing' });
  const [serverErrors, setServerErrors] = useState<FieldErrors | undefined>();
  const [failure, setFailure] = useState<unknown>(null);
  const busyRef = useRef(false);
  /** An earlier attempt of this form had an unknown outcome: a 409 may be our own product */
  const hadUnknown = useRef(false);

  const open = (product: ProductOut, notice: AdminProductNotice) =>
    navigate(`/admin/productos/${product.id}`, { state: { notice } });

  async function check(data: ProductIn) {
    setPhase({ name: 'checking', data });
    const result = await findCreatedProduct(data);
    if (result.status === 'same') open(result.product, 'recovered');
    else if (result.status === 'different') setPhase({ name: 'different', data, product: result.product });
    else if (result.status === 'absent') setPhase({ name: 'absent', data });
    else setPhase({ name: 'unknown', data, check: result });
  }

  async function send(data: ProductIn) {
    if (busyRef.current) return; // a double click sends one request
    busyRef.current = true;
    setFailure(null);
    setServerErrors(undefined);
    setPhase({ name: 'sending', data });
    try {
      open(await createProduct(data), 'created');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        if (hadUnknown.current) await check(data);
        else {
          setServerErrors({ sku: 'Ya existe un producto con este SKU.' });
          setPhase({ name: 'editing' });
        }
      } else if (error instanceof ApiError && error.status === 422) {
        setServerErrors(fieldErrorsFromIssues(error.issues));
        setFailure(error.issues.length === 0 ? error : null);
        setPhase({ name: 'editing' });
      } else if (isUncertain(error)) {
        hadUnknown.current = true;
        await check(data);
      } else {
        setFailure(error);
        setPhase({ name: 'editing' });
      }
    } finally {
      busyRef.current = false;
    }
  }

  async function recheck(data: ProductIn) {
    if (busyRef.current) return;
    busyRef.current = true;
    try {
      await check(data);
    } finally {
      busyRef.current = false;
    }
  }

  const locked = phase.name === 'sending' || phase.name === 'checking' || phase.name === 'unknown';

  return (
    <section className="admin-product">
      <nav className="breadcrumb" aria-label="Ruta">
        <Link to="/admin">Administración</Link>
        <span aria-hidden="true">/</span>
        <Link to="/admin/productos">Productos</Link>
        <span aria-hidden="true">/</span>
        <span>Nuevo</span>
      </nav>
      <header className="page-header">
        <h1>Nuevo producto</h1>
        <p className="page-header__lead">El producto queda activo y visible en el catálogo. Sus existencias se registran después, desde su página.</p>
      </header>

      {failure !== null && <ErrorAlert error={failure} title="No se creó el producto." />}

      {phase.name === 'checking' && (
        <Alert tone="info">
          <Spinner label="No recibimos la respuesta. Comprobando si el producto se creó…" />
        </Alert>
      )}
      {phase.name === 'unknown' && (
        <Alert tone="warning" title="No sabemos si el producto se creó.">
          <p>
            {phase.check.reason === 'incomplete'
              ? 'La búsqueda por nombre tiene más resultados de los que se pudieron revisar, así que no podemos confirmarlo.'
              : 'No pudimos consultar los productos para comprobarlo.'}{' '}
            Si vuelves a intentarlo y ya existía, el SKU único impedirá un duplicado.
          </p>
          <div className="form-actions">
            <Button variant="secondary" onClick={() => void recheck(phase.data)}>
              Volver a comprobar
            </Button>
            <Button variant="secondary" onClick={() => void send(phase.data)}>
              Intentar crear de nuevo
            </Button>
            <Button variant="ghost" onClick={() => setPhase({ name: 'editing' })}>
              Editar el formulario
            </Button>
          </div>
        </Alert>
      )}
      {phase.name === 'absent' && (
        <Alert tone="info" title="El producto no se creó.">
          <p>Revisamos todos los productos con ese nombre y ninguno tiene el SKU {phase.data.sku}. Puedes intentarlo de nuevo.</p>
          <div className="form-actions">
            <Button variant="secondary" onClick={() => void send(phase.data)}>
              Crear de nuevo
            </Button>
          </div>
        </Alert>
      )}
      {phase.name === 'different' && (
        <Alert tone="error" title={`Ya existe un producto con el SKU ${phase.data.sku}.`}>
          <p>
            Tiene otros datos ({phase.product.name}), así que no lo consideramos creado por este formulario. Usa otro SKU o{' '}
            <Link to={`/admin/productos/${phase.product.id}`}>revisa ese producto</Link>.
          </p>
        </Alert>
      )}

      <ProductForm
        idPrefix="new-product"
        values={values}
        onChange={setValues}
        serverErrors={serverErrors}
        locked={locked}
        busy={phase.name === 'sending'}
        submitLabel="Crear producto"
        onSubmit={(data) => void send(data)}
        actions={
          <Link to="/admin/productos" className="btn btn--ghost">
            Cancelar
          </Link>
        }
      />
    </section>
  );
}
