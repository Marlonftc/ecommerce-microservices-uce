// Quantity field with − and + buttons. It edits a text value; the caller validates
// it with parseQuantity before sending anything.
interface QuantityInputProps {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  /** Highest value the + button reaches */
  max: number;
  disabled?: boolean;
  error?: string | undefined;
  /** Keeps the label for screen readers only (e.g. inside a cart line) */
  hideLabel?: boolean;
}

export function QuantityInput({ id, label, value, onChange, max, disabled = false, error, hideLabel = false }: QuantityInputProps) {
  const text = value.trim();
  const current = /^\d+$/.test(text) ? Number(text) : null;
  const ceiling = Math.max(1, max);

  function step(delta: number) {
    const base = current ?? 1;
    onChange(String(Math.min(Math.max(base + delta, 1), ceiling)));
  }

  return (
    <div className={`quantity${error ? ' quantity--invalid' : ''}`}>
      <label htmlFor={id} className={hideLabel ? 'visually-hidden' : 'field__label'}>
        {label}
      </label>
      <div className="quantity__controls">
        <button
          type="button"
          className="quantity__step"
          aria-label="Restar una unidad"
          onClick={() => step(-1)}
          disabled={disabled || (current !== null && current <= 1)}
        >
          −
        </button>
        <input
          id={id}
          className="quantity__input"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          maxLength={3}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
        <button
          type="button"
          className="quantity__step"
          aria-label="Sumar una unidad"
          onClick={() => step(1)}
          disabled={disabled || (current !== null && current >= ceiling)}
        >
          +
        </button>
      </div>
      {error && (
        <p id={`${id}-error`} className="field__error">
          {error}
        </p>
      )}
    </div>
  );
}
