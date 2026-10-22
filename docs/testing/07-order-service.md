# Pruebas de order-service

Fecha: 2026-10-21
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

order-service tiene tres contenedores con la misma imagen: `order-migrate` (migración), `order-service` (API) y `order-worker` (operaciones pendientes, expiración y barrido).

## O-01 Pruebas automatizadas

**Objetivo:** verificar carrito, checkout, pagos, ejecutor de operaciones, worker, administración y esquema, contra PostgreSQL real. Los otros servicios se **simulan** en estas pruebas.

**Preparación:** las pruebas usan su propio esquema de PostgreSQL (`order_tests`) dentro de `orders_db`. Los contenedores usan `public`, por lo que un `order-worker` en ejecución no ve los datos de prueba (ver hallazgo 3).

**Comandos:**
```powershell
cd backend\order-service
pytest -q
```

**Resultado esperado:** todas las pruebas pasan, sin omitidas, con el `order-worker` del sistema en ejecución.

**Resultado observado:**
```text
281 passed in 50.44s
```

| Archivo | Qué cubre |
|---|---|
| `test_health.py`, `test_config.py` | Salud, configuración, migración solo con variables de la base, lease y heartbeat coherentes con los timeouts |
| `test_*_client.py` | Contratos HTTP con los otros servicios y clasificación de errores (transitorios, de negocio, permanentes) |
| `test_models.py`, `test_schema.py` | 29 restricciones CHECK, claves únicas, índice parcial de un pedido activo por usuario y tipos de columna |
| `test_cart.py` | Carrito: versión, cantidades, producto inactivo, product-service caído, `CART_LOCKED`, `CART_FULL`, versiones viejas |
| `test_orders.py` | Checkout: idempotencia, sin stock, inventory caído, reintento, respuesta tardía, consultas y autorización |
| `test_payment.py` | Pago aprobado, rechazado, SLOW_APPROVE, reintentos, pedido no pagable, servicios caídos tras el pago, atomicidad |
| `test_worker.py`, `test_admin.py` | Worker: expiración, barrido, cancelación, carril lento, lease vencido, dos workers, `SKIP LOCKED`, reactivación del admin |
| `test_*_concurrency.py` | Peticiones simultáneas reales (carrito, checkout, pago, ejecutores) |
| `test_worker_process.py` | Proceso `python -m app.worker` real: retoma trabajo guardado y se detiene con SIGTERM / CTRL+BREAK |

**Estado:** PASS

## O-02 Contenedores y Compose

**Objetivo:** comprobar la construcción, la migración, la seguridad de los contenedores, las redes y los puertos.

**Comandos:**
```powershell
function dc { docker compose -f compose.yaml -f compose.override.example.yaml -f compose.e2e.yaml @args }
docker compose -f compose.yaml config --quiet
dc up -d --build
dc ps -a
dc exec order-service id
dc exec order-service ls /run/secrets
dc exec order-worker id
dc exec order-worker ls /run/secrets
dc exec order-service python -c "import socket; print([socket.gethostbyname(h) for h in ('product-service','inventory-service','payment-service','notification-service','postgres')])"
docker compose -f compose.yaml config | Select-String "published"
curl.exe http://localhost:8006/health/ready
```

**Resultado esperado:** configuración válida, migración con código 0, los dos contenedores de order-service `healthy`, usuario sin privilegios, solo la clave pública en la API, ningún secreto en el worker, resolución por nombre y ningún puerto publicado en `compose.yaml`.

**Resultado observado:**
```text
config: 0
order-migrate      Exited (0)
order-service      Up (healthy)   127.0.0.1:8006->8000/tcp
order-worker       Up (healthy)   8000/tcp
(6 jobs Exited (0); user, product, inventory, payment, notification, order-service y order-worker healthy)

uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)     (order-service)
jwt_public_key
uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)     (order-worker)
ls: cannot access '/run/secrets': No such file or directory

['172.20.0.6', '172.20.0.8', '172.20.0.5', '172.20.0.4', '172.18.0.7']
(ninguna línea con "published")
{"status":"ok","database":"up"}
```

**Observaciones:**
- `8000/tcp` en `order-worker` es el `EXPOSE` de la imagen: no está publicado y el worker no escucha en él.
- Los servicios se resuelven en `ecommerce-net` (172.20.x) y PostgreSQL en `data-net` (172.18.x).
- `order-migrate` recibe solo `DB_NAME`, `DB_USER` y `DB_PASSWORD` y tiene desactivado el healthcheck HTTP de la imagen.
- El healthcheck del worker comprueba un archivo de heartbeat que el worker actualiza en cada ciclo y antes de cada operación.

**Estado:** PASS

## O-03 Pruebas de extremo a extremo

**Objetivo:** comprobar la compra completa y la recuperación ante fallos con los seis servicios **reales** en Docker, sin simulaciones.

**Preparación:**
- Las pruebas se ejecutan desde el equipo anfitrión contra los puertos `127.0.0.1` del override. Varias detienen, pausan, matan o escalan contenedores; hacerlo desde un contenedor exigiría montar el socket de Docker, que da control total sobre el anfitrión.
- `compose.e2e.yaml` acorta los tiempos solo para estas pruebas: reserva de 1 minuto, barrido de 1 minuto, ciclo del worker de 2 s, lease de 20 s.
- La cuenta de administrador se lee de `.env`. Cada prueba crea sus propios clientes (contraseñas aleatorias en memoria) y sus propios productos `E2E-...` con stock preparado por el admin. No se modificaron datos existentes ni se eliminaron volúmenes.

**Comandos:**
```powershell
docker compose -f compose.yaml -f compose.override.example.yaml -f compose.e2e.yaml up -d --build
backend\order-service\.venv\Scripts\python.exe -m pytest tests\e2e -v
docker compose -f compose.yaml -f compose.override.example.yaml up -d
```

**Resultado observado:**
```text
collected 12 items
test_1_approved_purchase PASSED
test_2_rejected_payment PASSED
test_3_slow_approve_resolved_by_the_worker PASSED
test_4_two_customers_for_the_last_unit PASSED
test_5_customer_cancels PASSED
test_6_order_expires PASSED
test_7_inventory_down_then_sweep PASSED
test_8_notification_down_then_delivered PASSED
test_9_worker_killed_while_holding_a_lease PASSED
test_10_restart_and_recreate_keep_everything PASSED
test_11_two_workers_without_duplicated_effects PASSED
test_12_graceful_stop PASSED
12 passed in 287.75s (0:04:47)
```

Cada prueba pasó solo si se cumplieron todas sus comprobaciones:

| # | Escenario | Comprobaciones (todas cumplidas) |
|---|---|---|
| 1 | Compra aprobada | Stock 5 → reserva de 2: `available 3, reserved 2`; tras el pago `PAID`, `CONFIRMED` y stock `3, 0` (la confirmación baja `reserved` sin volver a bajar `available`); un pago `APPROVED` por 51.00; una notificación `ORDER_PAID`; carrito vacío; las 3 operaciones `DONE` |
| 2 | Pago rechazado | `PAYMENT_REJECTED`, `RELEASED`, stock de vuelta a `5, 0`, una notificación `PAYMENT_REJECTED`, un pago `REJECTED`, carrito conservado y desbloqueado |
| 3 | SLOW_APPROVE | 202 con `PAYMENT_PENDING` y `REQUESTED`, con el pago ya `APPROVED` en payment-service; el **worker real** lo dejó `PAID` sin reintento del cliente; un solo pago; `RESOLVE_PAYMENT` con 2 o más intentos; `ORDER_PAID` entregada; stock `2, 0` |
| 4 | Última unidad | Dos clientes a la vez por un producto con stock 1: uno 201 y otro 409 `INSUFFICIENT_STOCK`; stock `0, 1`; la liberación del pedido perdedor (lápida) no liberó la unidad del ganador |
| 5 | Cancelación | `CANCELLED`/`USER`, stock de `1, 3` a `4, 0`, una notificación `ORDER_CANCELLED`; repetir la cancelación respondió 200 sin una segunda notificación |
| 6 | Expiración | Con reserva de 1 minuto, el worker canceló con `EXPIRED`, el stock volvió a `2, 0` y llegó `ORDER_CANCELLED` |
| 7 | inventory detenido | Checkout 202 con `RESERVING`/`PENDING`; tras levantar inventory, el barrido canceló con `RESERVATION_TIMEOUT`, la liberación quedó `DONE`, stock intacto (`4, 0`) y **sin** notificación |
| 8 | notification detenido | El pedido quedó `PAID` y `CONFIRMED` con `ORDER_PAID` pendiente; al levantar el servicio, el worker la entregó: una sola notificación |
| 9 | Worker matado con un lease | notification-service pausado; la prueba esperó a ver el lease del **worker** sobre `ORDER_PAID` (`locked_until` y 2 o más intentos) y entonces ejecutó `docker compose kill order-worker`; la operación siguió reclamada; al reiniciar el worker y vencer el lease se completó: una sola notificación, pedido `PAID`, stock `2, 0` |
| 10 | Persistencia | Con una notificación pendiente: `restart` y recreación de `order-service` y `order-worker` (contenedores con otro ID); el pedido siguió `PAID` y la operación pendiente; el worker nuevo la completó |
| 11 | Dos workers | `--scale order-worker=2`, ambos healthy; 4 compras con notificación pendiente; cada cliente recibió exactamente una `ORDER_PAID` y cada stock quedó `2, 0` |
| 12 | Parada ordenada | `docker compose stop order-worker`: `exited` con código 0 y en el log "stop requested" y "worker stopped" |

**Observación sobre el escenario 9:** la llamada del worker matado pudo llegar a notification-service al reanudarse; el reintento la repite y el `event_id` garantiza que el cliente reciba una sola notificación. El sistema no promete que una llamada HTTP ocurra exactamente una vez, sino que repetirla no duplica efectos de negocio.

**Estado:** PASS

## Hallazgos durante las pruebas

**1. CHECK que podía evaluarse como NULL** (Etapa 2). La regla `status <> 'PAID' OR payment_status = 'APPROVED'` aceptaba un pedido `PAID` sin estado de pago. Se reescribió con `COALESCE` y se comprobó en PostgreSQL que la versión ingenua aceptaba la fila.

**2. Doble clic que respondía CART_LOCKED** (Etapa 4). La misma clave enviada 10 veces a la vez respondía a veces 409 en lugar del pedido original (7 de 10 ejecuciones correctas). Una petición veía el carrito bloqueado por el pedido de su "gemela" y no volvía a buscar su clave. Se corrigió y se agregó una prueba determinista que falla con el error.

**3. Pruebas unitarias e `order-worker` compartiendo la base** (Etapa 7). Al levantar el worker en Docker, 2 pruebas del worker fallaron en la primera ejecución: el worker real tomaba las operaciones creadas por las pruebas y las ejecutaba contra los servicios reales. Dejó 13 notificaciones de usuarios de prueba, que se borraron (`DELETE 13`), y 3 lápidas en inventory (ids 47 a 49), identificadas por su hora de creación (19:24, con el worker ya activo). Los pagos de usuarios de prueba fueron 0. Corrección: las pruebas usan su propio esquema (`order_tests`). Se reprodujo el fallo con un worker real activo y, con la corrección, la suite pasó 3 veces seguidas con ese worker en ejecución.

**4. Imagen del worker sin `build`.** El primer `up --build` mostró `pull access denied for ecommerce-uce/order-service`, porque `order-worker` usaba la imagen sin indicar cómo construirla. Se agregó `build:`.

**5. Protecciones que las pruebas no detectaban.** Al quitar a propósito cada protección, tres no hacían fallar ninguna prueba: el `FOR UPDATE` del carrito (las creaciones simultáneas del carrito tapaban su falta), la condición `PAYMENT_PENDING` al marcar `PAID` y `SKIP LOCKED` en el worker. Se agregaron pruebas para cada caso; ahora fallan sin la protección.

## Limitaciones

- El gateway aún no existe; comprobar que no enruta `/internal` queda pendiente para su etapa.
- Las pruebas E2E crean usuarios `e2e-...@example.com` y productos `E2E-...` en las bases reales; no se borran para no eliminar datos del proyecto.
- La reserva expira según la hora de PostgreSQL (`now()`), no la del contenedor.
- Una llamada HTTP a otro servicio puede repetirse (por ejemplo, si el worker muere tras llamar). Todas las llamadas son idempotentes, por lo que la repetición no tiene efectos nuevos.
- En Docker Desktop para Windows, los secretos montados desde archivo aparecen con permisos amplios (ver `06-notification-service.md`).
