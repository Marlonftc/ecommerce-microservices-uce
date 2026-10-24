import { Link } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

// Administration panel. Each section links to its pages as soon as they exist; the
// final authorization of every call stays in the microservices.
const SECTIONS = [
  { title: 'Productos e inventario', text: 'Crear, editar, desactivar y reactivar productos; ajustar existencias y ver su historial.', to: '/admin/productos' },
  { title: 'Usuarios', text: 'Consultar las cuentas registradas, activarlas o desactivarlas y cambiar su rol.', to: '/admin/usuarios' },
  { title: 'Operaciones pendientes', text: 'Revisar los pasos de las compras que el sistema reintenta y reintentar los fallidos.', to: '/admin/operaciones' },
  { title: 'Notificaciones de clientes', text: 'Consultar, en modo de solo lectura, los avisos enviados a los clientes.', to: '/admin/notificaciones' },
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
      <ul className="admin-home__sections">
        {SECTIONS.map((section) => (
          <li key={section.title}>
            <h3>{section.to ? <Link to={section.to}>{section.title}</Link> : section.title}</h3>
            <p>{section.text}</p>
            {!section.to && <p className="admin-home__soon">Disponible en una próxima entrega.</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}
