import { useAuth } from '../auth/AuthContext';

// Stage 2: shows who is signed in as administrator and what the panel will contain.
// No administrative endpoint is called yet.
const SECTIONS = [
  { title: 'Productos', text: 'Crear, editar, desactivar y reactivar productos del catálogo.' },
  { title: 'Inventario', text: 'Ajustar existencias y consultar el historial de ajustes de cada producto.' },
  { title: 'Operaciones pendientes', text: 'Revisar los pasos de las compras que el sistema reintenta y reintentar los fallidos.' },
  { title: 'Usuarios', text: 'Consultar las cuentas registradas, activarlas o desactivarlas y cambiar su rol.' },
];

export function AdminHomePage() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <section className="admin-home">
      <h1>Administración</h1>
      <p>
        Sesión de administrador: <strong>{user.full_name}</strong> ({user.email}).
      </p>
      <h2>Secciones del panel</h2>
      <p className="admin-home__soon">Estas secciones se habilitarán en una etapa posterior del proyecto.</p>
      <ul className="admin-home__sections">
        {SECTIONS.map((section) => (
          <li key={section.title}>
            <h3>{section.title}</h3>
            <p>{section.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
