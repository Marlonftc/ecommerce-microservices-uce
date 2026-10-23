import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/errors';
import { useAuth } from '../auth/AuthContext';
import { validateRegistration, type RegisterErrors, type RegisterForm } from '../auth/validation';
import { ErrorAlert } from '../components/Alert';
import { Button } from '../components/Button';
import { FormField } from '../components/FormField';

const EMPTY: RegisterForm = { fullName: '', email: '', password: '', confirmPassword: '' };
// Names of the backend fields in 422 answers
const SERVER_FIELDS: Record<string, keyof RegisterForm> = { full_name: 'fullName', email: 'email', password: 'password' };

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<RegisterForm>(EMPTY);
  const [errors, setErrors] = useState<RegisterErrors>({});
  const [emailTaken, setEmailTaken] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const update = (field: keyof RegisterForm) => (event: { target: { value: string } }) => {
    setForm((f) => ({ ...f, [field]: event.target.value }));
    setErrors((e) => ({ ...e, [field]: undefined }));
    if (field === 'email') setEmailTaken(false);
  };

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const found = validateRegistration(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    const email = form.email.trim();
    try {
      const result = await register({ email, full_name: form.fullName.trim(), password: form.password });
      // Logged in: GuestOnly takes the user to /cuenta
      if (!result.loggedIn) {
        const problem =
          result.error instanceof ApiError && result.error.status === 429
            ? 'No se pudo iniciar sesión automáticamente por demasiados intentos. Espera un momento e inicia sesión.'
            : 'No se pudo iniciar sesión automáticamente. Inicia sesión para continuar.';
        navigate('/login', { replace: true, state: { registered: true, email, loginProblem: problem } });
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setEmailTaken(true);
      } else if (err instanceof ApiError && err.status === 422 && err.issues.length > 0) {
        const fromServer: RegisterErrors = {};
        for (const issue of err.issues) {
          const field = SERVER_FIELDS[issue.field];
          if (field) fromServer[field] = 'El servicio rechazó este valor. Revísalo.';
        }
        setErrors(fromServer);
        if (Object.keys(fromServer).length === 0) setError(err);
      } else {
        setError(err);
      }
    } finally {
      setForm((f) => ({ ...f, password: '', confirmPassword: '' }));
      setSubmitting(false);
    }
  }

  return (
    <section className="auth-page">
      <h1>Crear cuenta</h1>
      <p className="auth-page__lead">Con tu cuenta podrás comprar y seguir tus pedidos.</p>
      {error !== null && <ErrorAlert error={error} />}
      <form className="auth-form" onSubmit={onSubmit} noValidate>
        <FormField id="reg-name" label="Nombre completo" autoComplete="name" value={form.fullName} onChange={update('fullName')} error={errors.fullName} />
        <FormField
          id="reg-email"
          label="Correo"
          type="email"
          autoComplete="email"
          value={form.email}
          onChange={update('email')}
          error={emailTaken ? 'Ya existe una cuenta con este correo.' : errors.email}
        />
        {emailTaken && (
          <p className="auth-page__switch">
            <Link to="/login" state={{ email: form.email.trim() }}>
              Iniciar sesión con este correo
            </Link>
          </p>
        )}
        <FormField
          id="reg-password"
          label="Contraseña"
          type="password"
          autoComplete="new-password"
          value={form.password}
          onChange={update('password')}
          error={errors.password}
          hint="Al menos 8 caracteres."
        />
        <FormField
          id="reg-confirm"
          label="Repite la contraseña"
          type="password"
          autoComplete="new-password"
          value={form.confirmPassword}
          onChange={update('confirmPassword')}
          error={errors.confirmPassword}
        />
        <Button type="submit" loading={submitting}>
          Crear cuenta
        </Button>
      </form>
      <p className="auth-page__switch">
        ¿Ya tienes cuenta? <Link to="/login">Inicia sesión</Link>
      </p>
    </section>
  );
}
