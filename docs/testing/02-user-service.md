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

## Hallazgos durante las pruebas

**El administrador inicial no podía iniciar sesión.** El correo de ejemplo `admin@ecommerce.local` usaba un dominio de uso especial que el validador de correos rechaza. El seed lo creaba sin validar, pero el login sí validaba el formato, por lo que la cuenta era inaccesible. Corrección: el seed aplica la misma validación que el registro y se detiene con error si el correo no es válido, el login ya no valida el formato (solo busca el correo) y la plantilla usa `admin@example.com`. Se añadieron pruebas para ambos casos.

**El job de migración detuvo el arranque con un correo inválido.** Al levantar el sistema con el correo antiguo en `.env`, `user-migrate` terminó con `Exited (1)` y el mensaje `ADMIN_EMAIL 'admin@ecommerce.local' is not a valid email address`, y `user-service` no arrancó gracias a la condición `service_completed_successfully`. Tras corregir `.env`, el arranque fue normal.

## Limitaciones observadas

- En Docker Compose sin Swarm, los secrets de tipo archivo se montan desde el disco del anfitrión. En Windows aparecen con permisos `rwxrwxrwx`. En Docker Swarm o Kubernetes se montan en memoria con permisos de solo lectura.
