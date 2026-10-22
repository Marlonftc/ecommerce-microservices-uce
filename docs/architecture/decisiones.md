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
| D11 | JWT RS256 con clave privada solo en user-service; duración de 30 minutos, sin revocación en esta versión | Los demás servicios verifican pero no pueden emitir tokens. Un token emitido sigue siendo válido hasta expirar aunque el usuario se desactive |
| D12 | Jobs de migración de un solo uso por servicio | Migración controlada y única antes de escalar |
| D13 | Healthcheck sobre `/health/ready` + `restart: always` | El healthcheck informa; la política de reinicio recupera el proceso |
| D14 | Escalado de product-service con `--scale` y resolución DNS dinámica en Nginx | Servicio de mayor demanda esperada (hipótesis) |
| D15 | Sin broker de mensajes ni circuit breaker inicial | Proporcionalidad al alcance del examen |
| D16 | Productos e inventario se relacionan por `product_id`, sin claves foráneas entre bases | Cada servicio es dueño de sus datos. Los productos iniciales usan ids conocidos con datos semilla idempotentes, y el inventario admite existencias de cualquier producto creado después |
| D17 | `/internal` se protege en el gateway y también en cada microservicio (defensa en profundidad) | El gateway publica solo una lista de rutas y responde 404 a todo lo demás, incluidas variantes codificadas o con `..`, y elimina `X-Internal-Key` recibida del exterior. Cada servicio sigue exigiendo la clave interna: si el gateway fallara o alguien llegara a la red interna, la segunda barrera sigue activa |
| D18 | Upstreams de Nginx con `zone` y `resolve`, resolver de Docker `127.0.0.11` con `valid=10s` y `resolver_timeout 5s` | Las réplicas nuevas de product-service se usan y las eliminadas se descartan sin reiniciar Nginx. Con un servicio sin contenedores, el DNS de Docker tarda unos segundos en responder `NXDOMAIN`; con 2 s Nginx abandonaba la consulta y conservaba la IP anterior, con 5 s recibe la respuesta y la descarta (ver `docs/testing/08-gateway.md`, H-02) |
