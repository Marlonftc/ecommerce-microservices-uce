# Pruebas de inventory-service

Fecha: 2026-10-21
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

## I-01 Pruebas automatizadas

**Objetivo:** verificar las reglas del inventario: ajustes, idempotencia, validación de productos, reservas y concurrencia.

**Procedimiento:**
```powershell
cd backend\inventory-service
pytest -q
cd ..\product-service
pytest -q
```

**Resultado esperado:** todas las pruebas pasan.

**Resultado observado:**
```text
inventory-service: 51 passed in 3.29s
product-service:   33 passed in 2.41s
```

| Archivo | Qué cubre |
|---|---|
| `test_models.py` | La base rechaza stock negativo, estados inválidos, reservas duplicadas por pedido, cantidades en 0, productos repetidos en una reserva, ajustes de 0 y claves de ajuste repetidas |
| `test_seed.py` | Carga única del stock inicial, ids y SKU iguales al catálogo, no sobrescribe movimientos posteriores, un registro de historial por producto |
| `test_stock.py` | Visibilidad pública y de admin, ajustes relativos sin tocar `reserved`, rechazo de ajustes que dejarían stock negativo, idempotencia, validación con product-service (existe, no existe, caído) e historial |
| `test_reservations.py` | Clave interna obligatoria, reserva todo o nada, idempotencia, contenido distinto, confirmación, liberación, transiciones inválidas, lápida y funcionamiento con product-service caído |
| `test_concurrency.py` | Peticiones simultáneas reales contra PostgreSQL (ver I-02) |
| `test_internal.py` (product-service) | El endpoint interno exige la clave interna, devuelve productos inactivos y rechaza tokens de usuario |

**Estado:** Aprobada

## I-02 Concurrencia y sobreventa

**Objetivo:** demostrar que no hay sobreventa ni bloqueos mutuos con solicitudes simultáneas.

**Procedimiento:** `tests/test_concurrency.py` lanza hilos que empiezan al mismo tiempo, cada uno con su propia conexión a PostgreSQL. Se repitió 20 veces seguidas.

| Escenario | Resultado esperado | Resultado observado |
|---|---|---|
| 10 compradores por la última unidad | Exactamente 1 reserva | 1 reserva, 9 rechazos, stock `(0, 1)` |
| 20 pedidos simultáneos con productos en orden opuesto | Todos reservan, sin deadlock | 20 reservas, 0 deadlocks en el log de PostgreSQL |
| El mismo pedido enviado 5 veces a la vez | Se reserva una sola vez | Stock `(7, 3)` |
| El mismo ajuste (+10) enviado 5 veces a la vez | Se aplica una sola vez | Stock de 10 a 20 y un único registro en el historial |

20 de 20 ejecuciones aprobadas.

**Estado:** Aprobada

## I-03 Ajustes administrativos

**Objetivo:** comprobar ajustes relativos, idempotencia, validación del producto, historial y comportamiento con product-service caído.

**Procedimiento:** en Swagger de inventory-service, con un token de administrador emitido por user-service.

| Paso | Acción | Resultado observado |
|---|---|---|
| a | `GET /inventory/1` como admin | `available: 20`, `reserved: 0` |
| b | Ajuste +5 con clave `prueba-1` | 201, de 15 a 20, `admin_user_id: 13`, `id: 52` |
| c | Repetir el mismo ajuste | 200, mismo `id: 52`, stock sigue en 20 |
| d | Misma clave con `delta: 7` | 409 `Idempotency-Key already used with different data` |
| e | Ajuste +10 de la cámara (id 9, inactiva) | 201, de 0 a 10 |
| f | Ajuste del producto 999 | 404 `Product does not exist` |
| g | `GET /inventory/1/adjustments` | 2 registros: +5 (15→20, admin 13) y carga inicial (0→15) |
| h | Ajuste con product-service detenido | 503 `product-service is not available, try again later` |
| i | `GET /inventory/1` con product-service detenido | 200, `available: 20`, `updated_at` sin cambios |

**Estado:** Aprobada

## I-04 Contenedores y comunicación por nombre de servicio

**Objetivo:** comprobar el arranque de los tres servicios, la comunicación entre contenedores por nombre y el aislamiento ante la caída de product-service.

**Procedimiento:**
```powershell
docker compose up -d --build
docker compose ps -a
docker compose logs inventory-migrate
docker compose exec inventory-service python -m app.check_catalog
docker compose exec inventory-service python -c "import socket; print(socket.gethostbyname('product-service'))"
docker compose exec inventory-service whoami
docker compose exec inventory-service ls /run/secrets
docker compose stop product-service
docker compose exec inventory-service python -m app.check_catalog
curl.exe http://localhost:8003/inventory/1
docker compose ps
docker compose start product-service
```

**Resultado observado:**
```text
Building 4.4s ... CACHED [builder 4/4] RUN pip install -r requirements.txt
inventory-migrate   Exited (0)        product-migrate   Exited (0)        user-migrate   Exited (0)
inventory-service   Up (healthy)      product-service   Up (healthy)      user-service   Up (healthy)

INFO:seed:initial stock: 0 products created, 8 already existed

product-service: http://product-service:8000
OK    id 1: expected LAP-001, product-service has LAP-001
...
OK    id 8: expected USB-001, product-service has USB-001
All initial products match.

172.20.0.2
appuser
jwt_public_key

(product-service detenido)
ERROR product-service not reachable: [Errno -2] Name or service not known
{"product_id":1,"available":20}
ecommerce-uce-inventory-service-1   Up 35 seconds (healthy)
```

**Observaciones:**
- La reconstrucción tardó 4.4 s frente a 54.5 s de la primera: la capa de dependencias se reutilizó desde la caché.
- El DNS interno de Docker resuelve `product-service` a la IP de su contenedor. Al detener el contenedor, el nombre desaparece del DNS.
- inventory-service arrancó y siguió `healthy` sin product-service, porque no depende de él para arrancar ni en su healthcheck.

**Estado:** Aprobada

## Hallazgos durante las pruebas

**Variable obligatoria olvidada en un job.** Al hacer obligatoria `INTERNAL_API_KEY` en product-service, se añadió al servicio pero no al job `product-migrate`, que comparte imagen y configuración. El job terminó con `Exited (1)` (`internal_api_key: Field required`) y product-service quedó en `Created` sin arrancar, gracias a `service_completed_successfully`. Mientras tanto inventory-service arrancó con normalidad. Corrección: se añadió la variable al job y se verificó, para los seis contenedores de servicios, que cada uno recibe todas las variables obligatorias.

**Contraseña visible en el log de error.** El mismo error mostraba los valores recibidos por la configuración, incluida la contraseña de la base de datos. Corrección: `hide_input_in_errors=True` en la configuración de los tres servicios. Se verificó que un error de configuración ya no incluye la contraseña.

## Limitaciones observadas

- Los ajustes administrativos dependen de product-service para validar el producto; si no está disponible responden 503. Las reservas y consultas no dependen de él.
- La cabecera `X-Internal-Key` es una clave compartida entre servicios. En producción se usaría autenticación mutua con certificados (mTLS) o una service mesh.
