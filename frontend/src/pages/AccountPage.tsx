import { useAuth } from '../auth/AuthContext';
import { formatDateTime } from '../utils/format';

export const ROLE_LABELS: Record<string, string> = { customer: 'Cliente', admin: 'Administrador' };

export function AccountPage() {
  const { user, expiresAt } = useAuth();
  if (!user) return null;
  return (
    <section className="account-page">
      <h1>Mi cuenta</h1>
      <dl className="details">
        <div>
          <dt>Nombre</dt>
          <dd>{user.full_name}</dd>
        </div>
        <div>
          <dt>Correo</dt>
          <dd>{user.email}</dd>
        </div>
        <div>
          <dt>Rol</dt>
          <dd>{ROLE_LABELS[user.role] ?? user.role}</dd>
        </div>
        <div>
          <dt>Cuenta creada</dt>
          <dd>{formatDateTime(user.created_at)}</dd>
        </div>
        {expiresAt !== null && (
          <div>
            <dt>Tu sesión vence</dt>
            <dd>{formatDateTime(new Date(expiresAt).toISOString())}</dd>
          </div>
        )}
      </dl>
      <p className="account-page__note">
        La sesión se guarda solo en esta pestaña del navegador. Cierra sesión al terminar, sobre todo en equipos compartidos.
      </p>
    </section>
  );
}
