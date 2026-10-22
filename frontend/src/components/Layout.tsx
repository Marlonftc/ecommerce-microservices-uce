import { NavLink, Outlet, Link } from 'react-router-dom';

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
          {/* Only pages that exist are linked; the rest are added stage by stage */}
          <nav className="site-nav" aria-label="Navegación principal">
            <NavLink to="/" end className="site-nav__link">
              Inicio
            </NavLink>
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
