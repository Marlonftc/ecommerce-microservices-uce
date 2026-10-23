import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { useCart } from '../cart/CartContext';
import { useNotifications } from '../notifications/NotificationsContext';

function BrandMark() {
  return (
    <svg className="brand__mark" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="7" fill="#1d4f91" />
      <path d="M8 10h2.4l2.2 9.2h9.6L24 12.5H12" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="14" cy="23.5" r="1.6" fill="#fff" />
      <circle cx="21" cy="23.5" r="1.6" fill="#fff" />
    </svg>
  );
}

/** Cart link with the number of units confirmed by order-service */
function CartLink() {
  const { cart, itemCount } = useCart();
  const label = cart ? `Carrito, ${itemCount} ${itemCount === 1 ? 'unidad' : 'unidades'}` : 'Carrito';
  return (
    <NavLink to="/carrito" className="site-nav__link" aria-label={label}>
      Carrito
      {cart && itemCount > 0 && (
        <span className="site-nav__count" aria-hidden="true">
          {itemCount}
        </span>
      )}
    </NavLink>
  );
}

function BellIcon() {
  return (
    <svg className="site-nav__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Zm4 4a2 2 0 0 0 4 0"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Bell of a customer with the unread count; an unknown count (error) is never shown as 0 */
function NotificationsLink() {
  const { status, unread } = useNotifications();
  let label = 'Notificaciones';
  if (status === 'error') label = 'Notificaciones (no se pudo consultar cuántas hay sin leer)';
  else if (unread !== null) label = `Notificaciones, ${unread} sin leer`;
  return (
    <NavLink to="/notificaciones" className="site-nav__link site-nav__bell" aria-label={label} title={label}>
      <BellIcon />
      {unread !== null && unread > 0 && (
        <span className="site-nav__count" aria-hidden="true">
          {unread > 99 ? '99+' : unread}
        </span>
      )}
      {status === 'error' && (
        <span className="site-nav__count site-nav__count--unknown" aria-hidden="true">
          ?
        </span>
      )}
    </NavLink>
  );
}

function SessionNav() {
  const { status, user, logout } = useAuth();
  const navigate = useNavigate();

  if (status === 'authenticated' && user) {
    return (
      <>
        <CartLink />
        <NavLink to="/pedidos" className="site-nav__link">
          {user.role === 'admin' ? 'Pedidos' : 'Mis pedidos'}
        </NavLink>
        {user.role === 'customer' && <NotificationsLink />}
        <NavLink to="/cuenta" className="site-nav__link">
          Mi cuenta
        </NavLink>
        {user.role === 'admin' && (
          <NavLink to="/admin" className="site-nav__link">
            Administración
          </NavLink>
        )}
        <span className="site-nav__user" title={user.email}>
          {user.full_name}
        </span>
        <button
          type="button"
          className="site-nav__link site-nav__button"
          onClick={() => {
            logout();
            navigate('/', { replace: true });
          }}
        >
          Cerrar sesión
        </button>
      </>
    );
  }
  if (status === 'anonymous') {
    return (
      <>
        <NavLink to="/login" className="site-nav__link">
          Iniciar sesión
        </NavLink>
        <NavLink to="/registro" className="site-nav__link site-nav__link--accent">
          Crear cuenta
        </NavLink>
      </>
    );
  }
  return null; // loading or unavailable: no session links yet
}

export function Layout() {
  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="container site-header__inner">
          <Link to="/" className="brand">
            <BrandMark />
            <span>E-Commerce UCE</span>
            <span className="brand__sub">Tienda de tecnología</span>
          </Link>
          <nav className="site-nav" aria-label="Navegación principal">
            <NavLink to="/" end className="site-nav__link">
              Inicio
            </NavLink>
            <NavLink to="/productos" className="site-nav__link">
              Catálogo
            </NavLink>
            <SessionNav />
          </nav>
        </div>
      </header>
      <main className="site-main" id="contenido">
        <div className="container">
          <Outlet />
        </div>
      </main>
      <footer className="site-footer">
        <div className="container site-footer__inner">
          <p>Universidad Central del Ecuador, Carrera de Sistemas de Información</p>
          <p>Plataforma de comercio electrónico basada en microservicios</p>
        </div>
      </footer>
    </div>
  );
}
