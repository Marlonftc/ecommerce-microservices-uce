interface SpinnerProps {
  /** Visible text; an empty string shows only the circle (the text stays for screen readers) */
  label?: string;
}

export function Spinner({ label = 'Cargando…' }: SpinnerProps) {
  return (
    <span className="spinner" role="status">
      <span className="spinner__circle" aria-hidden="true" />
      {label ? <span>{label}</span> : <span className="visually-hidden">Cargando…</span>}
    </span>
  );
}
