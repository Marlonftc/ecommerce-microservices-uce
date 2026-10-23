# Pruebas de user-service

Fecha: 2026-10-20
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

## U-01 Pruebas automatizadas

**Objetivo:** verificar la lógica del servicio: salud, contraseñas, tokens, registro, login, roles y datos semilla.

**Procedimiento:**
```powershell
cd backend\user-service
pytest -q
```
Las pruebas que usan base de datos se ejecutan dentro de una transacción que se revierte al terminar.

**Resultado esperado:** todas las pruebas pasan.

**Resultado observado:**
```text
26 passed in 7.38s
```

| Archivo | Qué cubre |
|---|---|
| `test_health.py` | `/health/live` y respuesta 503 cuando la base no responde |
| `test_security.py` | Hash bcrypt, verificación y sal aleatoria |
| `test_tokens.py` | Token válido, expirado, otra audiencia, otra clave, modificado, sin firma (`alg: none`) y confusión de algoritmo (HS256 con la clave pública) |
| `test_auth.py` | Registro, correo duplicado, intento de elegir rol, datos inválidos, login, mismo error ante credenciales incorrectas, `/users/me` y control por rol |
| `test_seed.py` | El seed se detiene si el correo del admin no es válido |
| `test_users_admin.py` | Administración de cuentas (añadido después, ver U-06) |

**Estado:** Aprobada

## U-02 Recuperación ante caída de la base

**Objetivo:** comprobar que el servicio no se cae cuando PostgreSQL deja de responder y que se recupera solo.

**Procedimiento:** con el servicio en ejecución, consultar `/health/ready`, detener PostgreSQL (`docker compose stop postgres`), consultar de nuevo, arrancarlo (`docker compose start postgres`) y consultar otra vez.

**Resultado esperado:** 200, luego 503, luego 200 sin reiniciar el servicio.

**Resultado observado:**
```text
"GET /health/ready HTTP/1.1" 200 OK
WARNING:user-service:database not reachable: (psycopg.errors.ConnectionTimeout) connection timeout expired
"GET /health/ready HTTP/1.1" 503 Service Unavailable
"GET /health/ready HTTP/1.1" 200 OK
```

**Estado:** Aprobada

## U-03 Migraciones

**Objetivo:** comprobar que Alembic crea la tabla `users` y que la migración coincide con el modelo.

**Procedimiento:**
```powershell
alembic upgrade head
alembic current
alembic check
docker compose exec postgres psql -U user_svc -d users_db -c "\d users"
```

**Resultado esperado:** migración `0001` aplicada, sin diferencias con el modelo y tabla con índice único en `email` y restricción de roles.

**Resultado observado:**
```text
Running upgrade  -> 0001, create users table
0001 (head)
No new upgrade operations detected.
"ix_users_email" UNIQUE, btree (email)
"ck_users_role" CHECK (role::text = ANY (ARRAY['customer'::character varying, 'admin'::character varying]::text[]))
```

**Estado:** Aprobada

## U-04 Control de acceso por rol

**Objetivo:** comprobar que cada endpoint aplica la autorización correcta.

**Procedimiento:** en Swagger, registrar un cliente, iniciar sesión, autorizar con su token y consultar `/users/me` y `/users`. Repetir `/users` con el token del administrador.

**Resultado esperado y observado:**

| Usuario | `GET /users/me` | `GET /users` |
|---|---|---|
| Cliente | 200, sus datos, sin contraseña | 403 `"Admin role required"` |
| Administrador | 200 | 200, lista de usuarios |

**Estado:** Aprobada

## U-05 Contenedor

**Objetivo:** comprobar la construcción de la imagen, el orden de arranque y la seguridad del contenedor.

**Procedimiento:**
```powershell
docker compose build user-service
docker images ecommerce-uce/user-service
docker compose up -d
docker compose ps -a
docker compose logs user-migrate
docker compose exec user-service whoami
docker compose exec user-service ls -a /app
docker compose exec user-service ls -l /run/secrets
```

**Resultado esperado:** `user-migrate` termina con código 0, `user-service` arranca después y queda `healthy`, corre sin privilegios, sin archivos innecesarios y con las claves montadas como secrets.

**Resultado observado:**
```text
ecommerce-uce/user-service:latest   DISK USAGE 320MB   CONTENT SIZE 74.6MB
ecommerce-uce-user-migrate-1   Exited (0)
ecommerce-uce-user-service-1   Up About a minute (healthy)   127.0.0.1:8001->8000/tcp
INFO:seed:admin admin@gmail.com already exists
appuser
.  ..  alembic  alembic.ini  app
-rwxrwxrwx 1 root root 1704 jwt_private_key
-rwxrwxrwx 1 root root  451 jwt_public_key
```
En la consulta de `/users` desde Swagger el contenedor respondió 200.

**Estado:** Aprobada

## U-06 Administración de cuentas: pruebas automatizadas

Ampliación posterior: `PATCH /users/{id}` y `GET /users?ids=` (decisiones D11 y D19).

**Objetivo:** verificar autenticación, autorización, activación, desactivación, roles, validaciones, compatibilidad del listado paginado y concurrencia.

**Procedimiento:**
```powershell
cd backend\user-service
pytest -q
pytest -q tests\test_users_admin.py
```
La prueba de carrera (`test_two_admins_demoting_each_other_at_the_same_time`) no usa la transacción revertida: crea dos administradores reales, los modifica con dos peticiones simultáneas en sesiones independientes y los elimina al terminar.

**Resultado esperado:** todas las pruebas pasan y no quedan usuarios `race-*` en la base.

**Resultado observado** (entorno de validación local, sin Docker: PostgreSQL 16.15, Python 3.13):
```text
87 passed in 13.08s
```
La prueba de carrera se repitió 15 veces seguidas: 15 aprobadas, 0 usuarios `race-*` restantes.

| Grupo | Qué cubre |
|---|---|
| Autenticación y permisos | Sin token 401, cliente 403 (también con cuerpo inválido), token de un admin desactivado 401 |
| Cambios válidos | Desactivar (el token del usuario deja de servir en `/users/me`), reactivar, promover, degradar, ambos campos a la vez, repetir valores actuales (200) |
| Errores | Usuario inexistente 404; id `0`, `-1`, `abc` o mayor que el máximo de la columna 422; cuerpo vacío, `null`, rol inválido, `is_active` no booleano (`"true"`, `1`, `"quizá"`) y campos desconocidos 422 sin cambios en la base |
| Propia cuenta | Cualquier PATCH sobre la propia cuenta, incluso sin efecto, 409 `CANNOT_MODIFY_SELF` |
| Revalidación | El actor desactivado o degradado mientras esperaba es rechazado, aunque SQLAlchemy tenga en memoria la versión anterior |
| Invariante | Sin otro administrador activo, `LAST_ACTIVE_ADMIN` |
| `?ids=` | 401/403, solo existentes, ordenados por id, sin duplicados, incluye inactivos, 100 valores aceptados contando duplicados, 101 rechazados, valores vacíos/no numéricos/fuera de rango 422, combinación con `limit` u `offset` 422, sin `password_hash` |
| Compatibilidad | `GET /users` sin parámetros y con `limit`/`offset` responde igual; `limit=0`, `limit=101`, `offset=-1` siguen dando 422 |
| Carrera real | A degrada a B y B degrada a A al mismo tiempo: una 200, otra 403, solo se aplica un cambio y queda al menos un administrador activo |

**Comprobación de la calidad de las pruebas:** se introdujeron 13 errores a propósito en el código (quitar el bloqueo, quitar la relectura del actor, permitir modificar la propia cuenta, aceptar `null`, aceptar booleanos no estrictos, contar los ids después de quitar duplicados, quitar el orden, etc.). Las pruebas detectaron 12. El que no se detecta es quitar la llamada al invariante dentro de `update_user`: no puede activarse desde la API, porque el actor revalidado es un administrador activo distinto del destino; la función del invariante se prueba por separado.

**Primera ejecución en el equipo con Docker:** `1 failed, 86 passed`. Falló la prueba antigua `test_admin_can_list_users` (ver hallazgo H-01). Corregida la prueba, en el entorno local:

| Usuarios en la base (+2 que crea la prueba) | Suite completa | Prueba sola | Archivos en orden inverso |
|---|---|---|---|
| 36 | 87 passed | 1 passed | 87 passed |
| 66 | 87 passed | 1 passed | 87 passed |
| 198 (200 en total: páginas exactas de 100) | 87 passed | 1 passed | 87 passed |
| 253 | 87 passed | 1 passed | 87 passed |

Los usuarios adicionales se insertaron solo para esta verificación en la base local y se eliminaron al terminar.

**Resultado en el equipo con Docker** (Windows, base del contenedor PostgreSQL, después de la corrección H-01):
```text
87 passed in 10.95s
```

**Estado:** Aprobada

## U-07 Administración de cuentas a través del gateway

**Objetivo:** comprobar las rutas reales `/api/users/{id}` y `/api/users?ids=` con tokens obtenidos mediante login.

**Procedimiento** (PowerShell, con el sistema levantado; `$gw` es el puerto del gateway en `.env`):
```powershell
$gw = "http://127.0.0.1:8090/api"
$admin = (Invoke-RestMethod -Method Post "$gw/auth/login" -ContentType "application/json" `
  -Body '{"email":"<ADMIN_EMAIL>","password":"<ADMIN_PASSWORD>"}').access_token
$h = @{ Authorization = "Bearer $admin" }

Invoke-RestMethod "$gw/users?ids=1,2,2,999999" -Headers $h
Invoke-RestMethod -Method Patch "$gw/users/<ID_CLIENTE>" -Headers $h -ContentType "application/json" -Body '{"is_active":false}'
Invoke-RestMethod -Method Patch "$gw/users/<ID_CLIENTE>" -Headers $h -ContentType "application/json" -Body '{"is_active":true}'
```
No se deben pegar tokens ni contraseñas en documentos ni capturas.

**Resultado observado** (entorno local con el gateway Nginx y los seis servicios reales): 32 comprobaciones, 32 correctas.

| Caso | Resultado |
|---|---|
| Admin `GET ?ids=` con duplicados e inexistentes | 200, solo existentes, ordenados, sin `password_hash` |
| Cliente / sin token `GET ?ids=` | 403 / 401 |
| `ids` vacío, `a`, `0`, `1,,2`, 101 valores, `ids` + `limit` | 422 |
| Cliente / sin token `PATCH` | 403 / 401 |
| Usuario inexistente, `role: null`, cuerpo vacío | 404, 422, 422 |
| Admin sobre su propia cuenta | 409 `CANNOT_MODIFY_SELF` |
| Desactivar cliente / su token en `/api/users/me` / reactivar / repetir | 200 / 401 / 200 / 200 |
| Promovido, token anterior: `GET /api/users` / `GET /api/admin/operations` | 200 / 403 (order-service lee el rol del token) |
| Promovido tras nuevo login: `GET /api/admin/operations` | 200 |
| Degradado: `GET /api/users` / `GET /api/admin/operations` con su token de admin | 403 / 200 (limitación D11) |
| Carrera A↔B con ambas peticiones esperando el bloqueo a la vez | Una 200 y otra 403; queda uno de los dos como admin activo |

**Estado:** Aprobada en el entorno local. Pendiente repetir en el equipo con Docker.

## Hallazgos durante las pruebas

**H-01: `test_admin_can_list_users` dependía de la cantidad de usuarios en la base.** La prueba pedía `GET /users` sin parámetros, que devuelve solo la primera página (50 usuarios por defecto, ordenados por id), y esperaba encontrar ahí a los dos usuarios que acababa de crear. Con más de 50 usuarios guardados, por ejemplo los que dejan las pruebas E2E, esos usuarios quedaban en otra página y la prueba fallaba aunque el servicio funcionaba bien. Se reprodujo con el código anterior a la ampliación (`1 failed, 25 passed` con 96 usuarios), por lo que no era una regresión. Corrección: la prueba recorre todas las páginas con `limit=100` y `offset` hasta recibir una página incompleta. No se cambió el endpoint ni sus valores por defecto, y la prueba no necesita borrar usuarios existentes.

**El administrador inicial no podía iniciar sesión.** El correo de ejemplo `admin@ecommerce.local` usaba un dominio de uso especial que el validador de correos rechaza. El seed lo creaba sin validar, pero el login sí validaba el formato, por lo que la cuenta era inaccesible. Corrección: el seed aplica la misma validación que el registro y se detiene con error si el correo no es válido, el login ya no valida el formato (solo busca el correo) y la plantilla usa `admin@example.com`. Se añadieron pruebas para ambos casos.

**El job de migración detuvo el arranque con un correo inválido.** Al levantar el sistema con el correo antiguo en `.env`, `user-migrate` terminó con `Exited (1)` y el mensaje `ADMIN_EMAIL 'admin@ecommerce.local' is not a valid email address`, y `user-service` no arrancó gracias a la condición `service_completed_successfully`. Tras corregir `.env`, el arranque fue normal.

## Limitaciones observadas

- En Docker Compose sin Swarm, los secrets de tipo archivo se montan desde el disco del anfitrión. En Windows aparecen con permisos `rwxrwxrwx`. En Docker Swarm o Kubernetes se montan en memoria con permisos de solo lectura.
- Desactivar o degradar una cuenta solo tiene efecto inmediato en user-service. Los demás servicios aceptan el token anterior hasta que expira (D11).
