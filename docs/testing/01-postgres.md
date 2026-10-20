# Pruebas de PostgreSQL

Fecha: 2026-10-20
Entorno: Windows, Docker Desktop (Engine 29.9.1, Compose 5.6.0), imagen `postgres:16-alpine`.

## P-01 Inicialización de bases y usuarios

**Objetivo:** comprobar que el script `infra/postgres/init/01-init-databases.sh` crea una base y un usuario por servicio en el primer arranque.

**Procedimiento:**
```powershell
docker compose up -d postgres
docker compose ps
docker compose logs postgres | Select-String "init:"
```

**Resultado esperado:** el contenedor queda `healthy` y aparecen 6 mensajes `init:`.

**Resultado observado:**
```text
ecommerce-uce-postgres-1   postgres:16-alpine   ...   Up 28 seconds (healthy)   5432/tcp
init: creating database users_db for user user_svc
init: creating database products_db for user product_svc
init: creating database inventory_db for user inventory_svc
init: creating database orders_db for user order_svc
init: creating database payments_db for user payment_svc
init: creating database notifications_db for user notification_svc
```
El puerto aparece como `5432/tcp` sin publicar hacia el equipo anfitrión.

**Estado:** Aprobada

## P-02 Aislamiento entre bases

**Objetivo:** comprobar que un servicio solo puede conectarse a su propia base.

**Procedimiento:**
```powershell
docker compose exec postgres psql -U order_svc -d orders_db -c "select current_user, current_database();"
docker compose exec postgres psql -U order_svc -d users_db -c "select 1;"
docker compose exec postgres psql -U postgres -P pager=off -c "\l"
```

**Resultado esperado:** `order_svc` entra a `orders_db`, es rechazado en `users_db`, y cada base solo tiene permisos para su usuario.

**Resultado observado:**
```text
 current_user | current_database
--------------+------------------
 order_svc    | orders_db

FATAL:  permission denied for database "users_db"
DETAIL:  User does not have CONNECT privilege.
```
En `\l`, cada base de servicio muestra solo a su usuario en *Access privileges* (por ejemplo `order_svc=CTc/order_svc`) y la base `postgres` muestra `=T/postgres`, sin permiso de conexión para `PUBLIC`.

**Estado:** Aprobada

**Observación:** `template1` conserva el permiso de conexión para `PUBLIC`. Es una plantilla sin datos, por lo que se consideró un riesgo bajo.

## P-03 Persistencia de datos

**Objetivo:** comprobar que los datos sobreviven a la eliminación del contenedor.

**Procedimiento:**
```powershell
docker compose exec postgres psql -U order_svc -d orders_db -c "create table prueba(msg text); insert into prueba values ('sobrevivo');"
docker compose down
docker volume ls
docker compose up -d postgres
docker compose exec postgres psql -U order_svc -d orders_db -c "select * from prueba;"
docker compose logs postgres | Select-String "init:"
```

**Resultado esperado:** tras eliminar y recrear el contenedor, el dato sigue existiendo y el script de inicialización no se vuelve a ejecutar.

**Resultado observado:**
```text
 ✔ Container ecommerce-uce-postgres-1 Removed

DRIVER    VOLUME NAME
local     ecommerce-uce_postgres-data

    msg
-----------
 sobrevivo
(1 row)
```
La búsqueda de mensajes `init:` en el contenedor nuevo no devolvió resultados.

**Estado:** Aprobada
