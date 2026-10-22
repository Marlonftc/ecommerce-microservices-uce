import { Link } from 'react-router-dom';

export function ForbiddenPage() {
  return (
    <section className="not-found">
      <h1>No tienes permiso para ver esta página</h1>
      <p>Esta sección es solo para administradores. Si necesitas acceso, pídeselo a un administrador.</p>
      <Link to="/" className="btn btn--secondary">
        Ir al inicio
      </Link>
    </section>
  );
}
