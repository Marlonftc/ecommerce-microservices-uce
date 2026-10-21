# Pruebas de notification-service

Fecha: 2026-10-21
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

## N-01 Pruebas automatizadas

**Objetivo:** verificar restricciones de la base, registro idempotente, validaciones, autorización, paginación, lectura, concurrencia y persistencia.

**Procedimiento:**
```powershell
cd backend\notification-service
pytest -q
```

**Resultado esperado:** todas las pruebas pasan, sin pruebas omitidas.

**Resultado observado:**
```text
82 passed in 10.03s
```

| Archivo | Qué cubre |
|---|---|
| `test_health.py` | Salud, base caída (503), errores de configuración sin contraseñas |
| `test_config.py` | Las migraciones funcionan solo con las variables de la base; la API exige la clave interna |
| `test_models.py` | Tipos válidos, `event_id` único y de máximo 100 caracteres, monto y moneda juntos o ambos vacíos, coherencia entre `is_read` y `read_at`, campos obligatorios, índice `(user_id, created_at)`; documenta que PostgreSQL redondea decimales sin error |
| `test_internal.py` | Registro nuevo (201), repetición idéntica (200, sin cambios), datos distintos (409, sin cambios), 17 eventos inválidos rechazados con 422, plantillas por tipo, clave interna obligatoria |
| `test_public.py` | Token obligatorio (sin token, inválido, vencido o firmado con otra clave), cada usuario ve solo lo suyo, el admin ve todo, orden, paginación con fechas iguales, filtro de no leídas, marcar como leída, lectura repetida, notificación ajena (404) |
| `test_concurrency.py` | Ver N-03 |
| `test_persistence.py` | Arranca uvicorn, registra y marca una notificación, detiene el proceso, arranca otro y comprueba que los datos y la idempotencia se conservan |

**Estado:** Aprobada

## N-02 Migración y modelo

**Objetivo:** comprobar que la tabla se crea según el modelo y con todas las restricciones.

**Procedimiento:**
```powershell
alembic upgrade head
alembic check
docker compose exec postgres psql -U notification_svc -d notifications_db -c "\d notifications"
```

**Resultado observado:**
```text
Running upgrade  -> 0001, create notifications table
No new upgrade operations detected.

Indexes:
    "notifications_pkey" PRIMARY KEY, btree (id)
    "ix_notifications_user_id_created_at" btree (user_id, created_at)
    "notifications_event_id_key" UNIQUE CONSTRAINT, btree (event_id)
Check constraints:
    "ck_notifications_amount_currency" CHECK (amount IS NULL AND currency IS NULL OR amount IS NOT NULL AND currency IS NOT NULL AND amount > 0::numeric AND currency::text = 'USD'::text)
    "ck_notifications_read_at" CHECK (is_read = (read_at IS NOT NULL))
    "ck_notifications_type" CHECK (type::text = ANY (ARRAY['ORDER_PAID'::character varying, 'PAYMENT_REJECTED'::character varying, 'ORDER_CANCELLED'::character varying]::text[]))
```
12 columnas; `read_at` y `created_at` son `timestamp with time zone`.

**Estado:** Aprobada

## N-03 Concurrencia

**Objetivo:** demostrar que un evento repetido a la vez no se registra dos veces ni produce errores 500.

**Procedimiento:** `tests/test_concurrency.py`, 10 peticiones HTTP que empiezan al mismo tiempo, cada una con su propia conexión a PostgreSQL. En el entorno de desarrollo se repitió 10 veces seguidas junto con la prueba de persistencia.

| Escenario | Resultado esperado | Resultado observado |
|---|---|---|
| El mismo evento 10 veces a la vez | 1 creada, el resto recibe la misma | 1×201 y 9×200, mismo id, 1 fila |
| El mismo `event_id` con 10 usuarios distintos | 1 creada y 9 conflictos | 1×201 y 9×409, ningún 500 |
| Marcar como leída 10 veces a la vez | Una sola fecha de lectura | 10×200 con la misma `read_at` |

10 de 10 ejecuciones aprobadas.

**Estado:** Aprobada

## N-04 Contenedor y Compose

**Objetivo:** comprobar el arranque en contenedor, la seguridad de la imagen, la clave JWT y la comunicación desde otro contenedor.

**Procedimiento:**
```powershell
function dc { docker compose -f compose.yaml -f compose.override.example.yaml @args }
docker compose -f compose.yaml config --quiet
dc up -d --build
dc ps -a
dc exec notification-service id
dc exec notification-service ls -l /run/secrets
dc exec notification-service python -c "from app.config import settings; from app.tokens import _public_key; print(settings.jwt_public_key_path, len(_public_key()), 'bytes')"
curl.exe -i http://localhost:8005/health/ready
curl.exe -i -X POST http://localhost:8005/internal/notifications
```
Después, desde inventory-service, dos POST idénticos a `http://notification-service:8000/internal/notifications` con la clave interna del contenedor, y una consulta `GET /notifications` con el token de `marlon@gmail.com` (id 12, rol `customer`) emitido por user-service.

**Resultado esperado:** configuración válida, jobs terminados con código 0, servicios `healthy`, usuario sin privilegios, solo la clave pública, 403 sin clave interna, 201 y luego 200 con el mismo id.

**Resultado observado:**
```text
config: 0
notification-migrate   Exited (0)
notification-service   Up (healthy)   127.0.0.1:8005->8000/tcp
(5 jobs Exited (0), 5 servicios Up (healthy), postgres Up (healthy))

uid=10001(appuser) gid=10001(appuser) groups=10001(appuser)
-rwxrwxrwx 1 root root 451 Oct 21 01:10 jwt_public_key
/run/secrets/jwt_public_key 451 bytes

HTTP/1.1 200 OK   {"status":"ok","database":"up"}
HTTP/1.1 403 Forbidden   {"detail":"Internal access only"}

201 76c33cb8-a5be-4ba6-9b11-163ac97a0d55
200 76c33cb8-a5be-4ba6-9b11-163ac97a0d55

GET /notifications -> total: 1, user_id: 12, type: ORDER_PAID, amount: "149.90",
title: "Pago confirmado", is_read: false, created_at: 2026-10-21T17:56:12.785511Z
```

**Observaciones:**
- El job `notification-migrate` recibe solo `DB_NAME`, `DB_USER` y `DB_PASSWORD`: las migraciones no cargan la configuración de JWT ni la clave interna.
- El log del job no muestra `Running upgrade` porque la tabla ya existía desde N-02; Alembic no tuvo cambios que aplicar y terminó con código 0.
- Las fechas se devuelven en UTC (sufijo `Z`).

**Estado:** Aprobada

## N-05 Persistencia tras reiniciar y recrear el contenedor

**Objetivo:** comprobar que las notificaciones y su estado de lectura no dependen del contenedor del servicio.

**Procedimiento:**
```powershell
curl.exe -s -X PATCH http://localhost:8005/notifications/<id>/read -H "Authorization: Bearer <token>"
dc restart notification-service
curl.exe -s http://localhost:8005/notifications -H "Authorization: Bearer <token>"
docker ps -q -f name=notification-service
dc up -d --force-recreate --no-deps notification-service
docker ps -q -f name=notification-service
curl.exe -s http://localhost:8005/notifications -H "Authorization: Bearer <token>"
```
PostgreSQL y su volumen no se detuvieron ni se eliminaron.

**Resultado observado:**

| Paso | Resultado observado |
|---|---|
| Marcar como leída | `is_read: true`, `read_at: 2026-10-21T17:57:04.618041Z` |
| Tras `restart` | Misma notificación, misma `read_at` |
| Recreación del contenedor | ID `9024d1332e02` → `9227518559e3`, `Up (healthy)` |
| Tras la recreación | Misma notificación, misma `read_at` |

**Estado:** Aprobada

## N-06 Exposición pública y demás servicios

**Objetivo:** comprobar que `compose.yaml` no publica puertos y que los servicios anteriores siguen funcionando.

**Procedimiento:**
```powershell
docker compose -f compose.yaml config | Select-String "published"
8001..8005 | % { "$_ " + (curl.exe -s http://localhost:$_/health/ready) }
```

**Resultado observado:**
```text
(ninguna línea con "published")
8001 {"status":"ok","database":"up"}
8002 {"status":"ok","database":"up"}
8003 {"status":"ok","database":"up"}
8004 {"status":"ok","database":"up"}
8005 {"status":"ok","database":"up"}
```
El puerto 8005 solo se publica con `compose.override.example.yaml` y únicamente en `127.0.0.1`.

**Estado:** Aprobada

**Pendiente:** comprobar que el API Gateway no enruta `/internal`. El gateway aún no está implementado; se verificará en su etapa.

## Hallazgos durante las pruebas

**CHECK que podía evaluarse como NULL.** La forma intuitiva de la restricción, `(amount IS NULL AND currency IS NULL) OR (amount > 0 AND currency = 'USD')`, acepta `amount = NULL` con `currency = 'USD'`: la expresión da `NULL` y PostgreSQL acepta un CHECK que da `NULL`. Se comprobó insertando esa fila con la versión ingenua. La restricción final agrega `IS NOT NULL` en ambos campos y tiene una prueba específica.

**Puerto 8005 ocupado.** En el primer `up` el contenedor quedó en `Created` con el error `bind: Only one usage of each socket address`. La causa era el uvicorn local de una prueba anterior, que seguía escuchando en el mismo puerto. Al detenerlo, el servicio arrancó con normalidad.

**Pruebas que dependían de una tabla vacía.** Después de registrar una notificación real en N-04, 21 pruebas fallaron (`assert 1 == 0`), porque contaban todas las filas de la tabla, compartida con los contenedores. El servicio funcionaba correctamente. Corrección: las pruebas comparan el número de filas antes y después, y las de consulta usan usuarios de prueba propios (900001 y 900002). Se verificó con la tabla vacía, con la notificación real y con 31 filas adicionales.

## Limitaciones observadas

- En Docker Desktop para Windows, los secretos montados desde archivos aparecen con permisos `rwxrwxrwx` y propietario `root`. Para la clave pública no es un problema, pero en user-service la clave privada también es legible por cualquier usuario dentro de su contenedor. En un servidor Linux se ajustarían los permisos del archivo en el host o se usaría un gestor de secretos.
- La clave interna es compartida entre servicios; en producción se usaría autenticación mutua con certificados (mTLS).
- Las notificaciones solo se consultan en la aplicación; no se envían correos, SMS ni mensajes en tiempo real.
- La recuperación cuando notification-service no está disponible depende de los reintentos de order-service, que se probarán en esa etapa.
