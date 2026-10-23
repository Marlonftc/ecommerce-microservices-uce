import type { ReactNode } from 'react';
import { ApiError } from '../api/errors';

type Tone = 'info' | 'success' | 'warning' | 'error';

interface AlertProps {
  tone?: Tone;
  title?: string;
  children?: ReactNode;
  /** X-Request-ID shown so a failure can be found in the gateway log */
  requestId?: string | null;
}

export function Alert({ tone = 'info', title, children, requestId }: AlertProps) {
  // Errors and warnings are announced immediately to screen readers
  const role = tone === 'error' || tone === 'warning' ? 'alert' : 'status';
  return (
    <div className={`alert alert--${tone}`} role={role}>
      {title && <p className="alert__title">{title}</p>}
      {children}
      {requestId && <p className="alert__ref">Referencia: {requestId}</p>}
    </div>
  );
}

/** Shows an ApiError (or any error) with its user message and reference */
export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  if (error instanceof ApiError) {
    return (
      <Alert tone="error" title={title} requestId={error.requestId}>
        <p>{error.message}</p>
        {error.issues.length > 0 && (
          <ul>
            {error.issues.map((issue) => (
              <li key={`${issue.field}-${issue.message}`}>
                {issue.field ? `${issue.field}: ` : ''}
                {issue.message}
              </li>
            ))}
          </ul>
        )}
      </Alert>
    );
  }
  return (
    <Alert tone="error" title={title}>
      <p>Ocurrió un error inesperado en la aplicación.</p>
    </Alert>
  );
}
