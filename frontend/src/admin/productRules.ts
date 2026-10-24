// Product form: the same rules as ProductIn in product-service, which validates again on
// every request. Values are normalized the way the service stores them (SKU in capitals,
// category as "ropa-deportiva"), so the admin sees the final value before saving.
// The price is handled as text only: it is never converted to a floating point number.
import type { ProductIn, ProductOut } from '../api/types';

export const SKU_MIN = 3;
export const SKU_MAX = 32;
export const NAME_MIN = 2;
export const NAME_MAX = 120;
export const DESCRIPTION_MAX = 2000;
export const CATEGORY_MIN = 2;
export const CATEGORY_MAX = 50;
/** max_digits=12 with 2 decimal places: at most 10 digits before the separator */
export const PRICE_MAX_INTEGER_DIGITS = 10;

const SKU_PATTERN = /^[A-Z0-9]+(-[A-Z0-9]+)*$/;
const CATEGORY_PATTERN = /^[a-z0-9áéíóúñü]+(-[a-z0-9áéíóúñü]+)*$/;

export interface ProductFormValues {
  sku: string;
  name: string;
  description: string;
  category: string;
  price: string;
}

export type ProductField = keyof ProductFormValues;
export type FieldErrors = Partial<Record<ProductField, string>>;

export const EMPTY_FORM: ProductFormValues = { sku: '', name: '', description: '', category: '', price: '' };

export function normalizeSku(value: string): string {
  return value.trim().toUpperCase();
}

/** "  Ropa Deportiva " -> "ropa-deportiva", like normalize_category in product-service */
export function normalizeCategory(value: string): string {
  return value.trim().toLowerCase().split(/\s+/).filter(Boolean).join('-');
}

export type PriceResult = { ok: true; value: string } | { ok: false; error: string };

/**
 * Exact decimal text with two decimals ("899.9" -> "899.90", "12,5" -> "12.50").
 * Accepted: digits, optionally ONE separator (comma or point) followed by 1 or 2 digits.
 * Rejected as ambiguous: three digits after a single separator ("1.234" could be 1234),
 * several separators ("1.234,50"), and anything else (signs, spaces, exponents).
 */
export function parsePrice(raw: string): PriceResult {
  const text = raw.trim();
  if (!text) return { ok: false, error: 'Ingresa el precio.' };
  if (/^[1-9]\d{0,2}[.,]\d{3}$/.test(text) || /[.,].*[.,]/.test(text)) {
    return { ok: false, error: 'Formato ambiguo: no uses separadores de miles. Ejemplo: 1234.50 o 1234,50.' };
  }
  const match = /^(\d+)(?:[.,](\d{1,2}))?$/.exec(text);
  if (!match) {
    if (/^\d+[.,]\d{3,}$/.test(text)) return { ok: false, error: 'El precio admite como máximo 2 decimales.' };
    if (text.startsWith('-')) return { ok: false, error: 'El precio debe ser mayor que 0.' };
    return { ok: false, error: 'Escribe solo números, con coma o punto para los decimales. Ejemplo: 899.99' };
  }
  const integer = match[1]!.replace(/^0+(?=\d)/, '');
  const fraction = (match[2] ?? '').padEnd(2, '0');
  if (integer.length > PRICE_MAX_INTEGER_DIGITS) {
    return { ok: false, error: `El precio admite como máximo ${PRICE_MAX_INTEGER_DIGITS} dígitos antes de los decimales.` };
  }
  if (/^0+$/.test(integer) && fraction === '00') return { ok: false, error: 'El precio debe ser mayor que 0.' };
  return { ok: true, value: `${integer}.${fraction}` };
}

export type FormResult = { ok: true; data: ProductIn } | { ok: false; errors: FieldErrors };

export function validateProductForm(values: ProductFormValues): FormResult {
  const errors: FieldErrors = {};

  const sku = normalizeSku(values.sku);
  if (sku.length < SKU_MIN || sku.length > SKU_MAX) errors.sku = `El SKU debe tener entre ${SKU_MIN} y ${SKU_MAX} caracteres.`;
  else if (!SKU_PATTERN.test(sku)) errors.sku = 'Solo letras sin tilde, números y guiones entre ellos. Ejemplo: LAP-003';

  const name = values.name.trim();
  if (name.length < NAME_MIN || name.length > NAME_MAX) errors.name = `El nombre debe tener entre ${NAME_MIN} y ${NAME_MAX} caracteres.`;

  const description = values.description.trim();
  if (description.length > DESCRIPTION_MAX) errors.description = `La descripción admite hasta ${DESCRIPTION_MAX} caracteres.`;

  const rawCategory = values.category.trim();
  const category = normalizeCategory(values.category);
  if (rawCategory.length < CATEGORY_MIN || rawCategory.length > CATEGORY_MAX) {
    errors.category = `La categoría debe tener entre ${CATEGORY_MIN} y ${CATEGORY_MAX} caracteres.`;
  } else if (!CATEGORY_PATTERN.test(category)) {
    errors.category = 'Solo letras minúsculas, números y guiones. Ejemplo: ropa-deportiva';
  }

  const price = parsePrice(values.price);
  if (!price.ok) errors.price = price.error;

  if (Object.keys(errors).length > 0 || !price.ok) return { ok: false, errors };
  return { ok: true, data: { sku, name, description, category, price: price.value } };
}

/** Form values from a saved product (to edit it) */
export function formFromProduct(product: ProductOut): ProductFormValues {
  return {
    sku: product.sku,
    name: product.name,
    description: product.description,
    category: product.category,
    price: product.price,
  };
}

/** Same decimal amount as text, without floating point ("899.9" equals "899.90") */
export function samePrice(a: string, b: string): boolean {
  const pa = parsePrice(a);
  const pb = parsePrice(b);
  return pa.ok && pb.ok && pa.value === pb.value;
}

/** The saved product holds exactly the data that was sent */
export function matchesData(product: ProductOut, data: ProductIn): boolean {
  return product.sku === data.sku && product.name === data.name && (product.description ?? '') === (data.description ?? '')
    && product.category === data.category && samePrice(product.price, data.price);
}

/** 422 issues of FastAPI ("sku", "price"…) mapped to the fields of the form */
export function fieldErrorsFromIssues(issues: readonly { field: string; message: string }[]): FieldErrors {
  const fields: ProductField[] = ['sku', 'name', 'description', 'category', 'price'];
  const errors: FieldErrors = {};
  for (const issue of issues) {
    const field = fields.find((f) => issue.field === f || issue.field.endsWith(`.${f}`));
    if (field && !errors[field]) errors[field] = `El servidor rechazó este valor: ${issue.message}`;
  }
  return errors;
}
