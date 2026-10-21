# Pruebas de payment-service

Fecha: 2026-10-21
Entorno: Windows, Docker Desktop, Python 3.12.10 (local) e imagen `python:3.12-slim`.

## Y-01 Pruebas automatizadas

**Objetivo:** verificar pagos simulados, idempotencia, concurrencia, recuperación, validación de montos y acceso por usuario.

**Procedimiento:**
```powershell
cd backend\payment-service
pytest -q
```

**Resultado esperado:** todas las pruebas pasan.

**Resultado observado:**
```text
41 passed in 7.21s
```

| Archivo | Qué cubre |
|---|---|
| `test_health.py` | Salud, simulación desactivada por defecto, errores de configuración sin contraseñas |
| `test_models.py` | La base rechaza montos no positivos, monedas distintas de USD, estados y modos inválidos, dos pagos por pedido, claves repetidas y estados contradictorios con la fecha de procesamiento; documenta que PostgreSQL redondea decimales sin error |
| `test_internal.py` | Clave interna obligatoria, los tres modos de simulación, montos y monedas inválidos rechazados antes de la base, datos de tarjeta rechazados, idempotencia, un pago por pedido, simulación desactivada (503 sin registros), consulta por pedido |
| `test_public.py` | Token obligatorio, cada cliente ve solo sus pagos, el pago de otro usuario responde 404, el admin ve todos |
| `test_recovery.py` | Un pago dejado en PENDING se completa con el modo guardado; no se puede retomar con otros datos; los pagos resueltos no cambian |
| `test_concurrency.py` | Ver Y-02 |

**Estado:** Aprobada

## Y-02 Concurrencia y modo lento

**Objetivo:** demostrar que no se crean ni procesan dos pagos por pedido y que el modo lento no mantiene transacciones abiertas.

**Procedimiento:** `tests/test_concurrency.py`, con hilos que empiezan al mismo tiempo, cada uno con su propia conexión a PostgreSQL. Se repitió 10 veces seguidas.

| Escenario | Resultado esperado | Resultado observado |
|---|---|---|
| La misma petición 10 veces a la vez | 1 pago; todos reciben el mismo | 1 creado, mismo id para todos |
| 10 claves distintas para el mismo pedido a la vez | 1 pago y 9 conflictos | 1 creado, 9 conflictos |
| `SLOW_APPROVE` con espera de 2 s | APPROVED en la base antes de responder | APPROVED guardado mientras la respuesta seguía pendiente; respuesta tras 2 s; la consulta posterior responde de inmediato |
| Conexiones durante la espera | Ninguna transacción abierta | 0 sesiones `idle in transaction` en `pg_stat_activity` |

10 de 10 ejecuciones aprobadas.

**Estado:** Aprobada

## Y-03 Prueba manual en Swagger

**Objetivo:** comprobar el comportamiento observable de los pagos.

| Paso | Acción | Resultado observado |
|---|---|---|
| a | Pago `APPROVE` con clave `pago-1`, monto `749.99`, `user_id` 12 | 201, `APPROVED`, id `1a6a5ec4-...` |
| b | Repetir exactamente la petición | 200, mismo id y misma fecha de procesamiento |
| c | Pago `SLOW_APPROVE` con clave `pago-2`, monto `59.99` | 201 `APPROVED` tras la espera |
| d | `GET /payments` con el token de `marlon@gmail.com` emitido por user-service | 200 con sus 2 pagos, del más reciente al más antiguo |

**Estado:** Aprobada

## Y-04 Contenedor y comunicación por la red de Docker

**Objetivo:** comprobar el arranque, la seguridad del contenedor y que otro contenedor puede crear un pago llamando al servicio por su nombre.

**Procedimiento:**
```powershell
docker compose up -d --build
docker compose ps -a
docker compose logs payment-migrate
docker compose exec payment-service whoami
docker compose exec payment-service ls /run/secrets
docker compose exec payment-service printenv PAYMENT_SIMULATION_ENABLED
curl.exe http://localhost:8004/health/ready
docker compose exec inventory-service python -c "...httpx2.post('http://payment-service:8000/internal/payments', ...)"
```

**Resultado observado:**
```text
payment-migrate     Exited (0)        payment-service     Up (healthy)   127.0.0.1:8004->8000/tcp
(y los demás: 4 jobs Exited (0), 4 servicios Up (healthy))
appuser
jwt_public_key
true
{"status":"ok","database":"up"}
201 APPROVED
```

**Observación:** la reconstrucción tardó 42 s porque la imagen base `python:3.12-slim` se había actualizado (digest `sha256:2259...` el día anterior, `sha256:cb33...` este día) y la capa de dependencias tuvo que reconstruirse. Para builds totalmente reproducibles se puede fijar la imagen por digest.

**Estado:** Aprobada

## Hallazgo durante las pruebas

**Transacción abierta durante la espera de SLOW_APPROVE.** Después de confirmar el pago, la lectura de los valores finales (`refresh`) abría automáticamente una nueva transacción de lectura, que quedaba abierta durante los 8 segundos de espera. No afectaba al resultado funcional, pero ocupaba una conexión del pool. Lo detectó la prueba que consulta `pg_stat_activity` (`assert 1 == 0`). Corrección: antes de la espera, el pago se convierte en la respuesta final y se cierra la sesión. Se verificó que la prueba falla sin la corrección y pasa con ella.

## Limitaciones observadas

- El monto lo envía order-service y payment-service no lo recalcula: se confía en el llamador interno autenticado con la clave interna.
- Tras un pago rechazado, el mismo pedido no puede pagarse de nuevo; se debe crear otro pedido.
- Los pagos son simulados; no existe integración con una pasarela real ni se almacenan datos de tarjetas.
