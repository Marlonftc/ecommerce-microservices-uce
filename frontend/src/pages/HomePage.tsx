// Welcome page. Product browsing and stock queries belong to the catalogue.
import { Link } from 'react-router-dom';
import '../styles/home.css';

interface ServiceInfo {
  name: string;
  role: string;
  database: string;
}

const SERVICES: ServiceInfo[] = [
  { name: 'user-service', role: 'Cuentas, inicio de sesión y roles', database: 'users_db' },
  { name: 'product-service', role: 'Catálogo de productos', database: 'products_db' },
  { name: 'inventory-service', role: 'Existencias y reservas', database: 'inventory_db' },
  { name: 'order-service', role: 'Carrito, pedidos y coordinación de cada compra', database: 'orders_db' },
  { name: 'payment-service', role: 'Pagos simulados', database: 'payments_db' },
  { name: 'notification-service', role: 'Avisos al cliente', database: 'notifications_db' },
];

export function HomePage() {
  return (
    <div className="home-page">
      <section className="home-hero" aria-labelledby="home-title">
        <div className="home-hero__visual">
          <img src="/images/uce-campus.webp" width={800} height={450}
            decoding="async" alt="Instalaciones de la Universidad Central del Ecuador" />
        </div>
        <div className="home-hero__copy">
          <p className="home-eyebrow">Tecnología y accesorios</p>
          <h1 id="home-title">Tecnología para <em>lo que viene.</em></h1>
          <p className="home-hero__lead">Encuentra equipos y accesorios para estudiar, trabajar y dar forma a tus ideas.</p>
          <Link to="/productos" className="btn home-hero__cta">
            Explorar catálogo <span aria-hidden="true">→</span>
          </Link>
          <p className="home-hero__academic">Proyecto académico · Universidad Central del Ecuador</p>
        </div>
      </section>

      <section className="home-shopping-guide" aria-label="Tu compra, paso a paso">
        <div><span className="home-shopping-guide__step" aria-hidden="true">01</span><div><h2>Explora y compara</h2><p>Revisa las características y precios del catálogo.</p></div></div>
        <div><span className="home-shopping-guide__step" aria-hidden="true">02</span><div><h2>Prepara tu carrito</h2><p>Elige tus productos y ajusta las cantidades.</p></div></div>
        <div><span className="home-shopping-guide__step" aria-hidden="true">03</span><div><h2>Prueba la experiencia</h2><p>Completa el recorrido con un pago simulado, sin cobros reales.</p></div></div>
      </section>

      <details className="home-project">
        <summary>Acerca del proyecto y su arquitectura <span aria-hidden="true">+</span></summary>
        <div className="home-project__body">
          <h2>Una tienda de tecnología construida con seis microservicios</h2>
          <p>Cada parte del negocio funciona como un servicio independiente, con su propia base de datos. Así se puede actualizar, escalar o recuperar una parte sin detener el resto.</p>
          <div className="route" aria-label="Recorrido de una solicitud">
            <div className="route__stop route__stop--entry">
              <h3>Tu navegador</h3>
              <p>Usa solo la dirección de la tienda; nunca ve los servicios internos.</p>
            </div>
            <div className="route__stop route__stop--gateway">
              <h3>API Gateway</h3>
              <p>Nginx recibe cada solicitud en /api y la envía al servicio que corresponde.</p>
            </div>
            <ul className="route__services">
              {SERVICES.map((service) => (
                <li key={service.name} className="service">
                  <div className="service__name">{service.name}</div>
                  <p className="service__role">{service.role}</p>
                  <p className="service__db">Base de datos: {service.database}</p>
                </li>
              ))}
            </ul>
          </div>

          <div className="home-notes" aria-label="Características de la arquitectura">
            <div>
              <h3>Datos separados</h3>
              <p>Ningún servicio lee la base de datos de otro: se comunican por HTTP dentro de una red Docker.</p>
            </div>
            <div>
              <h3>Compras que se recuperan</h3>
              <p>order-service registra cada paso de la compra y lo reintenta si otro servicio falla.</p>
            </div>
            <div>
              <h3>Escalado por servicio</h3>
              <p>El catálogo, el servicio más consultado, puede ejecutarse en varias réplicas.</p>
            </div>
          </div>
        </div>
      </details>
    </div>
  );
}
