# Pruebas de product-service

Fecha: 2026-10-20 y 2026-10-21
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

## P-01 Pruebas automatizadas

**Objetivo:** verificar salud del servicio, catálogo inicial y endpoints del catálogo.

**Procedimiento:**
```powershell
cd backend\product-service
pytest -q
```
Las pruebas con base de datos se ejecutan dentro de una transacción que se revierte al terminar. Los tokens de prueba se firman con un par de claves temporal.

**Resultado esperado:** todas las pruebas pasan.

**Resultado observado:**
```text
28 passed in 1.89s
```

| Archivo | Qué cubre |
|---|---|
| `test_health.py` | `/health/live`, respuesta 503 con la base caída y cabecera `X-Instance` |
| `test_seed.py` | Carga única del catálogo, no sobrescribe cambios del admin, ids nuevos mayores que 8, precios exactos |
| `test_products.py` | Catálogo público, filtros, búsqueda (incluido `%` literal), paginación, consulta por varios ids, permisos 401/403, normalización de SKU y categoría, SKU duplicado, precios inválidos, campos desconocidos, actualización, desactivación, reactivación y tokens inválidos |

**Estado:** Aprobada

## P-02 Migración y catálogo inicial

**Objetivo:** comprobar que la tabla se crea según el modelo y que el catálogo inicial se carga una sola vez.

**Procedimiento:**
```powershell
alembic upgrade head
alembic check
python -m app.seed
python -m app.seed
docker compose exec postgres psql -U product_svc -d products_db -c "select id, sku, name, category, price from products order by id;"
```

**Resultado esperado:** migración aplicada sin diferencias con el modelo, 8 productos insertados la primera vez y ninguno la segunda.

**Resultado observado:**
```text
Running upgrade  -> 0001, create products table
No new upgrade operations detected.
INFO:seed:initial catalog: 8 products inserted, 0 already existed
INFO:seed:initial catalog: 0 products inserted, 8 already existed

 id |   sku   |                name                |    category    | price
----+---------+------------------------------------+----------------+--------
  1 | LAP-001 | Laptop 14 pulgadas Core i5 16 GB   | laptops        | 749.99
  ...
  8 | USB-001 | Memoria USB 64 GB                  | almacenamiento |   9.99
(8 rows)
```

**Estado:** Aprobada

## P-03 Administración del catálogo

**Objetivo:** comprobar la creación, desactivación y visibilidad de productos según el rol.

**Procedimiento:** en Swagger de product-service, con un token de administrador emitido por user-service:
1. `POST /products` con `sku: "cam-001"` y `category: "Accesorios"`.
2. `DELETE /products/9`.
3. `GET /products` sin token.
4. `GET /products/9` con token de administrador.

**Resultado esperado y observado:**

| Paso | Resultado observado |
|---|---|
| Crear | 201, `id: 9`, `sku: "CAM-001"`, `category: "accesorios"`, `price: "35.50"` |
| Desactivar | 204 sin cuerpo |
| Catálogo público | `total: 8`, el producto 9 no aparece |
| Detalle como admin | `is_active: false`, `updated_at` posterior a `created_at` |

**Estado:** Aprobada

## P-04 Contenedor e integración con user-service

**Objetivo:** comprobar el arranque en contenedor, la seguridad de la imagen y que un token emitido por user-service es aceptado por product-service sin comunicación entre ellos.

**Procedimiento:**
```powershell
docker compose up -d --build
docker compose ps -a
docker compose logs product-migrate
docker compose exec product-service whoami
docker compose exec product-service ls /run/secrets
curl.exe -i http://localhost:8002/health/ready
```
Después, iniciar sesión como administrador en user-service (puerto 8001) y usar ese token en `GET /products?include_inactive=true` de product-service (puerto 8002).

**Resultado esperado:** jobs terminados con código 0, servicios `healthy`, usuario sin privilegios, solo la clave pública montada y token aceptado.

**Resultado observado:**
```text
ecommerce-uce-product-migrate-1   Exited (0)
ecommerce-uce-product-service-1   Up About a minute (healthy)   127.0.0.1:8002->8000/tcp
ecommerce-uce-user-migrate-1      Exited (0)
ecommerce-uce-user-service-1      Up About a minute (healthy)   127.0.0.1:8001->8000/tcp
INFO:seed:initial catalog: 0 products inserted, 8 already existed
appuser
jwt_public_key
HTTP/1.1 200 OK
x-instance: d5763394e967
{"status":"ok","database":"up"}
```
- Login en user-service: `"expires_in": 1800` (30 minutos).
- `GET /products?include_inactive=true` en product-service con ese token: 200, `total: 9`, `x-instance: d5763394e967`.

**Estado:** Aprobada

## Limitaciones observadas

- product-service confía en el rol incluido en el token. Si un usuario se desactiva o cambia de rol, los tokens ya emitidos siguen siendo aceptados hasta que expiran (30 minutos).
- El puerto 8002 publicado en `compose.override.yaml` es temporal: deberá retirarse antes de escalar el servicio a varias réplicas.
