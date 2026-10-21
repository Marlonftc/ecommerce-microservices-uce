# E-Commerce Microservices UCE

Plataforma de comercio electrónico basada en microservicios y Docker.

Universidad Central del Ecuador - Carrera de Sistemas de Información
Autor: Julio César Mendoza Tello

## Descripción

La plataforma separa las funciones de una tienda en línea en seis microservicios independientes, cada uno con su propia base de datos, que se comunican mediante APIs REST dentro de una red Docker.

| Servicio | Responsabilidad |
|---|---|
| user-service | Registro, inicio de sesión y roles |
| product-service | Catálogo de productos |
| inventory-service | Existencias y reservas |
| order-service | Carrito y pedidos |
| payment-service | Pagos simulados |
| notification-service | Notificaciones |

Además se incluyen un API Gateway (Nginx), un frontend (React) y PostgreSQL.

## Tecnologías

Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, Nginx, React, Docker y Docker Compose.

## Estructura

```text
backend/      microservicios
frontend/     aplicación web
gateway/      configuración de Nginx
infra/        inicialización de PostgreSQL
tests/        pruebas de integración y carga
docs/         documentación
compose.yaml
.env.example
```

## Requisitos

- Docker Desktop con WSL 2
- Git

## Ejecución

Pendiente.

## Limitaciones

- Mientras un pedido está en proceso de compra, el carrito no se puede modificar. Si el pago se aprueba, el carrito se vacía; si se rechaza o se cancela el pedido, se conserva.
- Se usa una sola instancia de PostgreSQL con una base de datos y un usuario por servicio, por lo que el aislamiento es lógico y no físico.
- Los pagos y las notificaciones son simulados.
- Los tokens de acceso duran 30 minutos. Desactivar un usuario o cambiar su rol no invalida los tokens que ya tenía: los servicios que solo verifican la firma seguirán aceptándolos hasta que expiren.

Las decisiones de diseño están en [docs/architecture/decisiones.md](docs/architecture/decisiones.md).
