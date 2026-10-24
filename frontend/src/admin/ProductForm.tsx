// Form of a product, shared by "new" and "edit". It only collects and checks values; the
// pages decide what to send. The saved value of SKU and category is previewed, because
// product-service normalizes them.
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { listCategories } from '../api/products';
import type { ProductIn } from '../api/types';
import { Button } from '../components/Button';
import { FormField } from '../components/FormField';
import {
  CATEGORY_MAX, DESCRIPTION_MAX, NAME_MAX, SKU_MAX, normalizeCategory, normalizeSku, validateProductForm,
  type FieldErrors, type ProductField, type ProductFormValues,
} from './productRules';

interface ProductFormProps {
  idPrefix: string;
  values: ProductFormValues;
  onChange(values: ProductFormValues): void;
  /** Errors to show (from the server); local errors are added on submit */
  serverErrors?: FieldErrors;
  /** The form cannot be edited (e.g. while an unknown outcome is being resolved) */
  locked?: boolean;
  busy?: boolean;
  submitLabel: string;
  onSubmit(data: ProductIn): void;
  /** Extra buttons next to the submit button */
  actions?: ReactNode;
}

export function ProductForm({ idPrefix, values, onChange, serverErrors, locked = false, busy = false, submitLabel, onSubmit, actions }: ProductFormProps) {
  const [errors, setErrors] = useState<FieldErrors>({});
  const [categories, setCategories] = useState<string[]>([]);

  // Existing categories as suggestions; the form works without them
  useEffect(() => {
    const controller = new AbortController();
    listCategories(controller.signal)
      .then((scan) => {
        if (!controller.signal.aborted) setCategories(scan.categories);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (serverErrors) setErrors(serverErrors);
  }, [serverErrors]);

  const set = (field: ProductField) => (value: string) => {
    onChange({ ...values, [field]: value });
    if (errors[field]) setErrors((e) => ({ ...e, [field]: undefined }));
  };

  function submit(event: FormEvent) {
    event.preventDefault();
    if (locked || busy) return;
    const result = validateProductForm(values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    onSubmit(result.data);
  }

  const sku = normalizeSku(values.sku);
  const category = normalizeCategory(values.category);
  const disabled = locked || busy;

  return (
    <form className="product-form" onSubmit={submit} noValidate>
      <fieldset disabled={disabled} className="product-form__fields">
        <FormField
          id={`${idPrefix}-sku`}
          label="SKU"
          value={values.sku}
          maxLength={SKU_MAX + 10}
          autoComplete="off"
          error={errors.sku}
          hint={sku && sku !== values.sku ? `Se guardará como ${sku}` : 'Letras, números y guiones. Ejemplo: LAP-003'}
          onChange={(e) => set('sku')(e.target.value)}
        />
        <FormField
          id={`${idPrefix}-name`}
          label="Nombre"
          value={values.name}
          maxLength={NAME_MAX + 10}
          error={errors.name}
          onChange={(e) => set('name')(e.target.value)}
        />
        <div className={`field${errors.description ? ' field--invalid' : ''}`}>
          <label htmlFor={`${idPrefix}-description`} className="field__label">
            Descripción (opcional)
          </label>
          <textarea
            id={`${idPrefix}-description`}
            className="field__input field__textarea"
            rows={4}
            value={values.description}
            maxLength={DESCRIPTION_MAX + 50}
            aria-invalid={errors.description ? true : undefined}
            aria-describedby={errors.description ? `${idPrefix}-description-error` : undefined}
            onChange={(e) => set('description')(e.target.value)}
          />
          <p className="field__hint">
            {values.description.trim().length} de {DESCRIPTION_MAX} caracteres
          </p>
          {errors.description && (
            <p id={`${idPrefix}-description-error`} className="field__error">
              {errors.description}
            </p>
          )}
        </div>
        <FormField
          id={`${idPrefix}-category`}
          label="Categoría"
          value={values.category}
          maxLength={CATEGORY_MAX + 10}
          list={`${idPrefix}-categories`}
          autoComplete="off"
          error={errors.category}
          hint={category && category !== values.category ? `Se guardará como ${category}` : 'Elige una existente o escribe una nueva'}
          onChange={(e) => set('category')(e.target.value)}
        />
        <datalist id={`${idPrefix}-categories`}>
          {categories.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
        <FormField
          id={`${idPrefix}-price`}
          label="Precio (USD)"
          value={values.price}
          inputMode="decimal"
          autoComplete="off"
          error={errors.price}
          hint="Mayor que 0, hasta 2 decimales, con punto o coma. Ejemplo: 899.99"
          onChange={(e) => set('price')(e.target.value)}
        />
      </fieldset>
      <div className="form-actions">
        <Button type="submit" loading={busy} disabled={locked}>
          {submitLabel}
        </Button>
        {actions}
      </div>
    </form>
  );
}
