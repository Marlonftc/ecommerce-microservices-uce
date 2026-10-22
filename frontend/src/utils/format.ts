// Display formats for Ecuador (es-EC). The backend sends amounts as exact decimal
// strings; they are converted to numbers only for display.

const moneyFormatters = new Map<string, Intl.NumberFormat>();

export function formatMoney(value: string | number | null | undefined, currency = 'USD'): string {
  if (value === null || value === undefined || value === '') return '—';
  const amount = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(amount)) return '—';
  let formatter = moneyFormatters.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat('es-EC', { style: 'currency', currency, minimumFractionDigits: 2 });
    moneyFormatters.set(currency, formatter);
  }
  return formatter.format(amount);
}

const dateTimeFormatter = new Intl.DateTimeFormat('es-EC', { dateStyle: 'medium', timeStyle: 'short' });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : dateTimeFormatter.format(date);
}

/**
 * Compares two decimal amounts sent as strings ("749.9" and "749.90") without
 * converting them to floating point numbers.
 */
export function sameAmount(a: string, b: string): boolean {
  return normalizeDecimal(a) === normalizeDecimal(b);
}

function normalizeDecimal(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value.trim());
  if (!match) return value.trim();
  const whole = (match[2] ?? '').replace(/^0+(?=\d)/, '');
  const fraction = (match[3] ?? '').replace(/0+$/, '');
  const sign = whole === '0' && fraction === '' ? '' : match[1];
  return `${sign}${whole}${fraction ? `.${fraction}` : ''}`;
}
