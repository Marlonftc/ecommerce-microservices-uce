import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <section className="not-found">
      <h1>No encontramos esta página</h1>
      <p>La dirección no corresponde a ninguna página de la tienda. Revisa el enlace o vuelve al inicio.</p>
      <Link to="/" className="btn btn--secondary">
        Ir al inicio
      </Link>
    </section>
  );
}
