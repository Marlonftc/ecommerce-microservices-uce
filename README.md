# E-Commerce Microservices UCE

## Examen Complexivo - Universidad Central del Ecuador

Facultad de Ingeniería y Ciencias Aplicadas - Carrera de Sistemas de Información

**Caso 1:** Plataforma de comercio electrónico basada en microservicios y Docker.

**Autor:** Julio César Mendoza Tello

### Objetivo

Desarrollar una plataforma de comercio electrónico utilizando una arquitectura de microservicios contenerizados, aplicando principios de separación de responsabilidades, comunicación entre servicios, persistencia, escalabilidad y resiliencia.

> El enunciado no incluye el código de una aplicación monolítica existente. El análisis del monolito (AS-IS) es **conceptual**, construido a partir de las funcionalidades descritas en el caso.

### Tecnologías

- Python 3.12 + FastAPI, SQLAlchemy, Alembic, Pydantic
- PostgreSQL 16
- React + Vite
- Docker y Docker Compose
- Nginx (API Gateway)
- pytest y k6 (pruebas)
- Git y GitHub

### Microservicios

| Servicio | Responsabilidad |
|---|---|
| user-service | Registro, inicio de sesión, emisión de JWT (RS256) y roles |
| product-service | Catálogo: productos, precios y categorías |
| inventory-service | Existencias y reservas de unidades |
| order-service | Carrito, pedidos y coordinación de la compra (Saga orquestada) |
| payment-service | Pagos simulados e idempotentes |
| notification-service | Registro y consulta de notificaciones |

Componentes de apoyo (no son microservicios de negocio): API Gateway (Nginx), frontend (React) y PostgreSQL.

### Estructura del repositorio

```text
ecommerce-microservices-uce/
├── backend/            Un directorio por microservicio
├── frontend/           Aplicación web React + Vite
├── gateway/            Configuración del API Gateway (Nginx)
├── infra/postgres/     Script de inicialización de bases y roles
├── tests/
│   ├── integration/    Pruebas de flujo completo contra el gateway
│   └── load/           Pruebas de carga (k6)
├── docs/
│   ├── architecture/   Análisis, diagramas y decisiones
│   ├── testing/        Plan y resultados de pruebas
│   ├── evidence/       Evidencias de ejecución
│   └── defense/        Material para la defensa
├── compose.yaml        Orquestación con Docker Compose
├── .env.example        Plantilla de configuración (sin secretos)
└── README.md
```

### Requisitos previos

- Windows 10/11 con WSL 2
- Docker Desktop (Docker Engine 29+, Docker Compose v2+)
- Git
- Visual Studio Code
- 8 GB de RAM recomendados

### Instalación y ejecución

*Pendiente: se documentará cuando el primer microservicio sea ejecutable.*

### Flujo de trabajo con Git

- `main`: versión estable. Protegida; solo recibe cambios mediante Pull Request.
- `develop`: desarrollo e integración.
- Mensajes de commit con el formato *Conventional Commits* (`feat:`, `fix:`, `docs:`, `chore:`, `test:`).

### Avance del proyecto

- [x] Fase 1 - Análisis del problema y requisitos
- [x] Fase 2 - Diseño y aprobación de la arquitectura
- [x] Fase 3 - Configuración del entorno (VS Code, Git, GitHub, Docker)
- [ ] Fase 4 - Estructura base e implementación de microservicios *(en curso)*
- [ ] Fase 5 - Integración completa
- [ ] Fase 6 - Frontend
- [ ] Fase 7 - Pruebas de persistencia, resiliencia y escalabilidad
- [ ] Fase 8 - Documentación final y preparación de la defensa

### Limitaciones conocidas

- **Carrito bloqueado durante el checkout:** mientras un pedido está en un estado no terminal (`RESERVING`, `PENDING_PAYMENT` o `PAYMENT_PENDING`), el carrito del usuario no puede modificarse (respuesta `409`). Si el pago se aprueba, el carrito se vacía; si el pago se rechaza o el pedido se cancela, el usuario conserva su contenido. En consecuencia, cada usuario puede tener como máximo un checkout activo a la vez.
- **Aislamiento de datos lógico:** se utiliza una sola instancia PostgreSQL con una base y un rol por microservicio. No existe aislamiento físico: una caída de PostgreSQL afecta a todos los servicios con datos.
- **Pagos simulados:** no se integra ninguna pasarela real ni se almacenan datos de tarjetas.
- **Notificaciones internas:** no se envían correos ni SMS reales.

Las decisiones de arquitectura se registran en [`docs/architecture/decisiones.md`](docs/architecture/decisiones.md).
