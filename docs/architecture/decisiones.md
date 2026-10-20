# Registro de decisiones de arquitectura

Decisiones aprobadas antes de iniciar la implementación (Fase 4).

| # | Decisión | Justificación breve |
|---|---|---|
| D01 | Seis microservicios + gateway Nginx + frontend + PostgreSQL | Requisito del caso; los componentes de apoyo no son microservicios de negocio |
| D02 | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx | Ligero, validación automática y documentación Swagger integrada |
| D03 | Una instancia PostgreSQL, una base y un rol propietario por servicio, CONNECT revocado a PUBLIC | Propiedad de datos con bajo consumo de recursos; aislamiento lógico, no físico |
| D04 | Redes `ecommerce-net` y `data-net` (interna) | El gateway no puede alcanzar la base de datos |
| D05 | Saga orquestada por order-service | No existen transacciones SQL entre bases de datos distintas |
| D06 | Estados: RESERVING (interno), PENDING_PAYMENT, PAYMENT_PENDING, PAID, PAYMENT_REJECTED, CANCELLED | Cada intención se registra antes de actuar |
| D07 | Tabla `pending_operations` con reconciliación persistente (outbox simplificado) | Las operaciones pendientes sobreviven a reinicios |
| D08 | Idempotencia obligatoria en creación y pago de pedidos | Evitar efectos de negocio duplicados ante reintentos |
| D09 | Inventario sin temporizadores; expiración controlada por order-service | Evitar liberar stock de un pedido con pago en curso |
| D10 | Carrito bloqueado durante el checkout | Evitar inconsistencias entre carrito y pedido (ver limitación en README) |
| D11 | JWT RS256 con clave privada solo en user-service | Los demás servicios verifican pero no pueden emitir tokens |
| D12 | Jobs de migración de un solo uso por servicio | Migración controlada y única antes de escalar |
| D13 | Healthcheck sobre `/health/ready` + `restart: always` | El healthcheck informa; la política de reinicio recupera el proceso |
| D14 | Escalado de product-service con `--scale` y resolución DNS dinámica en Nginx | Servicio de mayor demanda esperada (hipótesis) |
| D15 | Sin broker de mensajes ni circuit breaker inicial | Proporcionalidad al alcance del examen |
