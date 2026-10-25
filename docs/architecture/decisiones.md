# Registro de decisiones de arquitectura

Decisiones aprobadas antes de iniciar la implementación (Fase 4). D19 y siguientes se añadieron durante el desarrollo.

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
| D11 | JWT RS256 con clave privada solo en user-service; duración de 30 minutos, sin revocación en esta versión | Los demás servicios verifican pero no pueden emitir tokens. Un token emitido sigue siendo válido hasta expirar aunque el usuario se desactive o cambie de rol (ver detalle abajo) |
| D12 | Jobs de migración de un solo uso por servicio | Migración controlada y única antes de escalar |
| D13 | Healthcheck sobre `/health/ready` + `restart: always` | El healthcheck informa; la política de reinicio recupera el proceso |
| D14 | Escalado de product-service con `--scale` y resolución DNS dinámica en Nginx | Servicio de mayor demanda esperada (hipótesis) |
| D15 | Sin broker de mensajes ni circuit breaker inicial | Proporcionalidad al alcance del examen |
| D16 | Productos e inventario se relacionan por `product_id`, sin claves foráneas entre bases | Cada servicio es dueño de sus datos. Los productos iniciales usan ids conocidos con datos semilla idempotentes, y el inventario admite existencias de cualquier producto creado después |
| D17 | `/internal` se protege en el gateway y también en cada microservicio (defensa en profundidad) | El gateway publica solo una lista de rutas y responde 404 a todo lo demás, incluidas variantes codificadas o con `..`, y elimina `X-Internal-Key` recibida del exterior. Cada servicio sigue exigiendo la clave interna: si el gateway fallara o alguien llegara a la red interna, la segunda barrera sigue activa |
| D18 | Upstreams de Nginx con `zone` y `resolve`, resolver de Docker `127.0.0.11` con `valid=10s` y `resolver_timeout 5s` | Las réplicas nuevas de product-service se usan y las eliminadas se descartan sin reiniciar Nginx. Con un servicio sin contenedores, el DNS de Docker tarda unos segundos en responder `NXDOMAIN`; con 2 s Nginx abandonaba la consulta y conservaba la IP anterior, con 5 s recibe la respuesta y la descarta (ver `docs/testing/08-gateway.md`, H-02) |
| D19 | Administración de cuentas con `PATCH /users/{id}` (solo `role` e `is_active`), sin borrado físico; cambios serializados con `pg_advisory_xact_lock` | Desactivar es reversible y conserva la relación con los pedidos. El bloqueo asesor, la relectura del administrador desde PostgreSQL y el bloqueo `FOR UPDATE` de la cuenta destino garantizan que siempre quede al menos un administrador activo, incluso con cambios simultáneos. Un administrador no puede modificar su propia cuenta |
| D20 | El frontend es un contenedor propio (Nginx sin privilegios) detrás del gateway; el gateway sirve React en `/` y la API en `/api` | Un solo origen y un solo puerto publicado: sin CORS y sin exponer otro puerto. El frontend solo está en `ecommerce-net`, con sistema de archivos de solo lectura y sin credenciales. Cualquier ruta fuera de `/api` devuelve `index.html` (navegación SPA); una ruta `/api` desconocida sigue en 404 JSON sin reenviarse, y las barreras de `/internal` se evalúan antes. El gateway no depende del frontend: si se detiene, la API sigue funcionando |
| D21 | Cabeceras de seguridad y CSP estricta (`'self'`, sin `unsafe-inline` ni `unsafe-eval`) declaradas una sola vez en el gateway | El build de Vite no tiene código en línea. En Nginx una `location` con `add_header` propio deja de heredar las del servidor, por eso las cabeceras están en un archivo incluido en el servidor y en `@rate_limited`. Sin HSTS ni `upgrade-insecure-requests`, porque el sistema se sirve por HTTP en 127.0.0.1. `VITE_PAYMENT_DEMO` es un valor de compilación: el build normal usa `false` y el de pruebas E2E usa `true` con otra etiqueta de imagen |
| D22 | Conservar el simulador de pagos para la entrega del complexivo; Stripe Checkout Sandbox queda como posible trabajo futuro | Preservar la estabilidad de la Saga, la idempotencia y la recuperación existentes. Los escenarios controlados permiten demostrar aprobación, rechazo y respuesta tardía sin depender de un proveedor externo; no se procesan tarjetas ni dinero real (ver detalle abajo) |

## D11: efecto de desactivar o cambiar el rol de un usuario

user-service consulta la base en cada petición, mientras que los demás servicios solo verifican la firma del JWT y leen el rol del token. Por eso:

| Cambio | En user-service | En los demás servicios |
|---|---|---|
| Desactivar una cuenta | Inmediato: el token deja de ser aceptado (401) | El token sigue siendo aceptado hasta expirar (máximo 30 minutos) |
| Quitar el rol de administrador | Inmediato: pierde el acceso de administrador (403) | Conserva los permisos de administrador del token hasta que expire |
| Promover a administrador | Inmediato | El token anterior sigue diciendo `customer`: el usuario debe iniciar sesión de nuevo para obtener un token con el nuevo rol |

La ausencia de revocación inmediata es una **limitación de seguridad del MVP**, aceptada por el alcance del examen. Se reduce con la duración corta del token. Las soluciones habituales (tokens de acceso más cortos con refresh tokens, una lista de revocación compartida o la consulta del estado del usuario en cada servicio) no se implementan en esta versión.

## D22: pagos simulados y alcance de la entrega académica

**Decisión aprobada el 2026-10-24:** mantener el simulador actual (opción B). Stripe Checkout Sandbox se considera una posible ampliación futura y no forma parte de la entrega actual.

El objetivo es demostrar la coordinación entre microservicios y su recuperación ante fallos con escenarios reproducibles. El simulador permite observar resultados de pago conocidos y una respuesta incierta para el llamador, conservando los contratos y las protecciones existentes. Incorporar una pasarela externa exigiría revisar persistencia, vencimientos y eventos asíncronos, con riesgo de regresiones en la Saga. Esta decisión delimita el alcance técnico; no constituye una integración financiera ni una certificación de pagos reales.

### Escenarios implementados

| Modo | Resultado en payment-service | Qué permite demostrar |
|---|---|---|
| `APPROVE` | `PENDING` → `APPROVED` | La Saga registra el pedido pagado y continúa con inventario y notificación. |
| `REJECT` | `PENDING` → `REJECTED` | El pedido pasa a `PAYMENT_REJECTED`, conserva el carrito y programa la liberación de inventario y la notificación. |
| `SLOW_APPROVE` | Guarda `APPROVED` antes de demorar la primera respuesta HTTP del pago creado | order-service puede recibir un timeout y conservar `PAYMENT_PENDING` hasta reconciliar el resultado, sin crear un segundo pago. |

**`SLOW_APPROVE` simula una respuesta HTTP tardía, no una autorización bancaria pendiente.** La espera ocurre después de confirmar el resultado y cerrar la sesión de base de datos; no mantiene una transacción ni un bloqueo durante la demora. Repetir la misma petición devuelve el pago ya guardado, sin aplicar otra vez la espera inicial. La demora predeterminada es de 8 segundos, superior al timeout de lectura predeterminado de 5 segundos de order-service; estos tiempos son configurables.

`PAYMENT_SIMULATION_ENABLED` habilita los tres modos y está desactivado por defecto. Si no se habilita, el endpoint de creación responde `503` sin crear un pago; no hay un proveedor alternativo configurado. `VITE_PAYMENT_DEMO` solo controla si la interfaz muestra las opciones de rechazo y respuesta tardía: ocultarlas no convierte el botón `Pagar` (`APPROVE`) en un pago real ni sustituye las validaciones del backend.

El comportamiento está implementado en [payment_service.py](../../backend/payment-service/app/services/payment_service.py) y [el router interno de pagos](../../backend/payment-service/app/routers/internal.py).

### Coordinación de la Saga y recuperación

order-service actúa como orquestador; cada servicio conserva sus datos y no existe una transacción SQL distribuida:

1. Crea el pedido con precios e importes calculados en el servidor, registra `RESERVING` y solicita la reserva de inventario. Cuando se confirma, pasa a `PENDING_PAYMENT` con vencimiento.
2. Al iniciar el pago, cambia condicionalmente a `PAYMENT_PENDING` y registra `RESOLVE_PAYMENT` en `pending_operations` dentro de la misma transacción local. Después llama a payment-service mediante la ruta interna autenticada, sin mantener la transacción SQL durante la llamada HTTP.
3. Ante `APPROVED`, cambia a `PAID`, vacía el carrito y registra `CONFIRM_INVENTORY` en una transacción local. Solo después de confirmar inventario programa `ORDER_PAID` para notification-service.
4. Ante `REJECTED`, registra `PAYMENT_REJECTED` y las operaciones de liberación y notificación. El carrito se conserva. Un fallo de red no se interpreta como rechazo.
5. El worker retoma las operaciones persistentes con leases y reintentos. Una caída posterior de inventario o notificaciones no revierte una aprobación: la operación pendiente se conserva. Los fallos permanentes requieren revisión y reactivación administrativa cuando corresponda.

La idempotencia se aplica en varios puntos: clave de creación del pedido por usuario, un pedido activo por usuario, clave interna de pago determinista `order:<UUID>`, unicidad de pago por pedido y claves únicas de las operaciones posteriores. Los cambios condicionados al estado esperado y los leases impiden que un ejecutor obsoleto vuelva a aplicar el resultado. Una llamada HTTP puede repetirse; la garantía buscada es evitar efectos de negocio duplicados, no prometer una única llamada HTTP.

La reserva de `PENDING_PAYMENT` vence a los 15 minutos por defecto. La expiración produce `CANCELLED` con motivo `EXPIRED` y programa liberación de inventario y notificación; `EXPIRED` no es un estado de pedido. **`PAYMENT_PENDING` no vence automáticamente**, para evitar liberar inventario mientras se desconoce el resultado del pago.

Los detalles están en [payment_flow.py](../../backend/order-service/app/services/payment_flow.py), [executor.py](../../backend/order-service/app/services/executor.py) y [recovery.py](../../backend/order-service/app/services/recovery.py). Las evidencias de validación ya registradas se conservan en [pruebas de payment-service](../testing/05-payment-service.md) y [pruebas de order-service](../testing/07-order-service.md); esta decisión documental no representa una nueva ejecución de esas pruebas.

### Limitaciones actuales

- No se solicitan, almacenan ni procesan datos de tarjetas; los contratos de pago rechazan campos adicionales de tarjeta.
- No se mueve dinero real ni se realizan autorizaciones, capturas o reembolsos financieros.
- No existe integración con Stripe ni recepción o verificación de webhooks de proveedores financieros.
- payment-service confía en el importe enviado por order-service mediante el contrato interno autenticado; no consulta a una entidad financiera.
- Los modos son deterministas: no reproducen todos los estados, autenticaciones o plazos de un proveedor real. Tras un rechazo, el mismo pedido no admite otro pago; se requiere un nuevo pedido.

### Stripe Checkout Sandbox como posible trabajo futuro

Una ampliación opcional debería conservar el simulador y elegir el proveedor de forma explícita, sin aprobar automáticamente mediante simulación cuando Stripe no responda. Antes de implementarla se deben resolver, al menos, estos puntos:

- **Vencimiento:** Checkout permite configurar la expiración automática de la sesión entre 30 minutos y 24 horas desde su creación, frente a la reserva local predeterminada de 15 minutos. Para mantener el plazo local habría que cerrar la sesión externa y confirmar que no se pagó antes de liberar inventario; ante un resultado incierto, continuar la reconciliación. No basta con aplicar el vencimiento actual a `PAYMENT_PENDING`. Véanse [creación](https://docs.stripe.com/api/checkout/sessions/create) y [expiración de sesiones](https://docs.stripe.com/api/checkout/sessions/expire).
- **Eventos asíncronos:** el retorno del navegador no debe marcar un pedido como pagado. Los webhooks requieren firma verificada sobre el cuerpo original, persistencia durable y tratamiento de duplicados y eventos desordenados. Los estados externos deben traducirse explícitamente al contrato local; un estado desconocido no equivale a rechazo. Véase [webhooks de Stripe](https://docs.stripe.com/webhooks).
- **Idempotencia:** conservar una intención y sesión vinculadas al pedido, claves estables ante timeouts y restricciones locales por sesión, pago y evento. La idempotencia de la API externa no sustituye estas garantías persistentes. Deben probarse concurrencia, reinicios y carreras entre pago y expiración. Véase [idempotencia de Stripe](https://docs.stripe.com/api/idempotent_requests).
- **Disponibilidad:** antes de retomar el trabajo, comprobar el acceso legítimo a una cuenta Sandbox desde Ecuador, sus permisos, los métodos disponibles y la conectividad para API y eventos. La disponibilidad comercial por país y el acceso a pruebas son cuestiones distintas; esta decisión no garantiza el acceso de una cuenta concreta. Consultar [países admitidos](https://stripe.com/global) y [configuración de cuentas y Sandbox](https://docs.stripe.com/get-started/account/set-up).

La ampliación requeriría autorización independiente y pruebas de regresión de ambos métodos. No se incorporan ahora dependencias, credenciales, rutas, estados, migraciones ni cambios de interfaz para Stripe.
