// Home page: explains how the store is built and leads to the catalogue.
import { Link } from 'react-router-dom';

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
    <>
      <section className="home-intro">
        <h1>Una tienda de tecnología construida con seis microservicios</h1>
        <p>
          Cada parte del negocio funciona como un servicio independiente, con su propia base de datos.
          Así se puede actualizar, escalar o recuperar una parte sin detener el resto.
        </p>
        <Link to="/productos" className="btn btn--primary home-intro__cta">
          Ver catálogo
        </Link>
      </section>

      <section aria-labelledby="route-title">
        <h2 id="route-title" className="visually-hidden">Recorrido de una solicitud</h2>
        <div className="route">
          <div className="route__stop route__stop--entry">
            <h2>Tu navegador</h2>
            <p>Usa solo la dirección de la tienda; nunca ve los servicios internos.</p>
          </div>
          <div className="route__stop route__stop--gateway">
            <h2>API Gateway</h2>
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
      </section>

      <section className="home-notes" aria-label="Características de la arquitectura">
        <div>
          <h2>Datos separados</h2>
          <p>Ningún servicio lee la base de datos de otro: se comunican por HTTP dentro de una red Docker.</p>
        </div>
        <div>
          <h2>Compras que se recuperan</h2>
          <p>order-service registra cada paso de la compra y lo reintenta si otro servicio falla.</p>
        </div>
        <div>
          <h2>Escalado por servicio</h2>
          <p>El catálogo, el servicio más consultado, puede ejecutarse en varias réplicas.</p>
        </div>
      </section>
    </>
  );
}
