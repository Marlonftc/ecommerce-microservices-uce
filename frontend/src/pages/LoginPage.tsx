import { useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ApiError } from '../api/errors';
import { NOTICE_MESSAGES, useAuth } from '../auth/AuthContext';
import { useCountdown } from '../auth/useCountdown';
import { Alert, ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { FormField } from '../components/FormField';

interface LoginState {
  from?: unknown;
  /** Set by the register page when the account was created but the automatic login failed */
  registered?: boolean;
  email?: string;
  loginProblem?: string;
}

export function LoginPage() {
  const { login, notice, clearNotice } = useAuth();
  const state = (useLocation().state ?? null) as LoginState | null;

  const [email, setEmail] = useState(state?.email ?? '');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [retryUntil, setRetryUntil] = useState<number | null>(null);
  const secondsLeft = useCountdown(retryUntil);

  const rateLimited = error instanceof ApiError && error.status === 429;
  const blocked = rateLimited && retryUntil !== null && secondsLeft > 0;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      // On success GuestOnly sends the user to the page they wanted
      await login(email, password);
    } catch (err) {
      setError(err);
      if (err instanceof ApiError && err.status === 429) {
        setRetryUntil(err.retryAfter !== null ? Date.now() + err.retryAfter * 1000 : null);
      }
    } finally {
      setPassword(''); // never kept after an attempt
      setSubmitting(false);
    }
  }

  return (
    <section className="auth-page">
      <h1>Iniciar sesión</h1>
      <p className="auth-page__lead">Entra con el correo y la contraseña de tu cuenta.</p>

      {state?.registered && (
        <Alert tone="success" title="Tu cuenta se creó correctamente.">
          <p>{state.loginProblem ?? 'Inicia sesión para continuar.'}</p>
        </Alert>
      )}
      {notice && (
        <Alert tone="warning">
          <p>{NOTICE_MESSAGES[notice]}</p>
        </Alert>
      )}
      {error !== null && (
        rateLimited ? (
          <Alert tone="warning" title="Demasiados intentos de inicio de sesión." requestId={error.requestId}>
            <p>
              {retryUntil !== null
                ? secondsLeft > 0
                  ? `Podrás intentarlo de nuevo en ${secondsLeft} s.`
                  : 'Ya puedes intentarlo de nuevo.'
                : 'Espera un momento antes de intentarlo de nuevo.'}
            </p>
          </Alert>
        ) : error instanceof ApiError && error.status === 401 ? (
          <Alert tone="error" requestId={error.requestId}>
            <p>Correo o contraseña incorrectos, o la cuenta está desactivada.</p>
          </Alert>
        ) : (
          <ErrorAlert error={error} />
        )
      )}

      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <FormField
          id="login-email"
          label="Correo"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (notice) clearNotice();
          }}
        />
        <FormField
          id="login-password"
          label="Contraseña"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Button type="submit" loading={submitting} disabled={blocked || !email.trim() || !password}>
          Iniciar sesión
        </Button>
      </form>
      <p className="auth-page__switch">
        ¿Aún no tienes cuenta? <Link to="/registro">Crea una</Link>
      </p>
    </section>
  );
}
