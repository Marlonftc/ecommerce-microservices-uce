# Pruebas del API Gateway

Fecha: 2026-10-21
Entorno: Windows, Docker Desktop, imagen `nginx:1.28.0-alpine`, Python 3.12.10 (pruebas).
Puerto: `GATEWAY_PORT=8090` en `.env` (ver hallazgo H-01).

## Configuración probada

| Elemento | Valor |
|---|---|
| Archivos | `gateway/Dockerfile`, `gateway/nginx.conf`, `gateway/conf.d/gateway.conf` |
| Puerto publicado | `127.0.0.1:${GATEWAY_PORT:-8080}:80`, el único de `compose.yaml` |
| Red | Solo `ecommerce-net`; sin acceso a `data-net` ni credenciales |
| Sistema de archivos | `read_only: true`, `tmpfs: /tmp` (pid y archivos temporales) |
| Salud | `GET /health`, responde Nginx sin consultar servicios; `HEALTHCHECK` con `wget` |
| Descubrimiento | `upstream` con `zone` y `server <servicio>:8000 resolve`; `resolver 127.0.0.11 valid=10s ipv6=off`; `resolver_timeout 5s` |
| Límites | `client_max_body_size 1m`, `proxy_connect_timeout 2s`, `proxy_read_timeout 30s` |
| Login | `limit_req` de 10 r/min por IP, `burst=5 nodelay`, respuesta 429 |
| Reintentos | Solo tras error de conexión rápido (`proxy_next_upstream error timeout`, máximo 5 s y 2 intentos). Nunca se repite una solicitud POST/PATCH ya enviada |

### Rutas públicas

El prefijo `/api` se elimina antes de reenviar. Cada ruta se declara como coincidencia exacta
(`= /api/products`) y como subruta (`/api/products/`), por lo que `/api/productsXYZ` no coincide.

| Gateway | Servicio | Ruta en el servicio |
|---|---|---|
| `/api/auth/...` | user-service | `/auth/...` (login con límite) |
| `/api/users`, `/api/users/...` | user-service | `/users`, `/users/...` |
| `/api/products`, `/api/products/...` | product-service | `/products`, `/products/...` |
| `/api/inventory`, `/api/inventory/...` | inventory-service | `/inventory`, `/inventory/...` |
| `/api/cart`, `/api/cart/...` | order-service | `/cart`, `/cart/...` |
| `/api/orders`, `/api/orders/...` | order-service | `/orders`, `/orders/...` |
| `/api/admin/operations`, `/api/admin/operations/...` | order-service | `/admin/operations`, ... |
| `/api/payments`, `/api/payments/...` | payment-service | `/payments`, `/payments/...` |
| `/api/notifications`, `/api/notifications/...` | notification-service | `/notifications`, ... |
| Cualquier otra | Nginx | 404 JSON, sin reenviar |

Los prefijos se verificaron en los routers de los seis servicios antes de escribir `proxy_pass`.
Ningún contrato de los servicios se modificó.

### Protección de `/internal`

1. Lista de rutas permitidas; todo lo demás responde 404.
2. Antes de elegir la ruta, se rechaza (404) cualquier ruta que contenga `internal` en cualquier
   combinación de mayúsculas, tanto en la forma original como ya decodificada, y cualquier ruta
   con `..`, `%2e`, `%2f`, `%5c` o `%25` (doble codificación). Ninguna ruta pública los necesita.
3. `X-Internal-Key` enviada por un cliente se elimina (`proxy_set_header X-Internal-Key ""`).
4. Cada servicio sigue exigiendo la clave interna en `/internal` (segunda barrera, decisión D17).

### Cabeceras

| Cabecera | Tratamiento |
|---|---|
| `Authorization`, `Idempotency-Key` | Se reenvían sin cambios |
| `X-Internal-Key` | Se elimina |
| `X-Forwarded-For`, `X-Real-IP` | Se reemplazan por la dirección que ve Nginx; los valores del cliente no se usan |
| `X-Request-ID` | Se acepta el del cliente solo si cumple `^[A-Za-z0-9-]{8,64}$`; si no, Nginx genera uno de 32 caracteres hexadecimales. Se envía al servicio y se devuelve al cliente |
| `Server` | `nginx`, sin versión (`server_tokens off`); la cabecera `server: uvicorn` de los servicios no se reenvía |

El log de acceso es JSON con `request_id`, método, ruta sin parámetros, estado, tiempos,
`upstream_addr` y `upstream_status`. No registra cabeceras, parámetros de consulta ni cuerpos,
por lo que nunca contiene JWT, contraseñas ni claves.

### Errores generados por Nginx

| Código | Cuándo | Cuerpo |
|---|---|---|
| 404 | Ruta no permitida | `{"detail":{"code":"NOT_FOUND",...}}` |
| 413 | Cuerpo mayor a 1 MB | `{"detail":{"code":"PAYLOAD_TOO_LARGE",...}}` |
| 429 | Exceso de intentos de login | `{"detail":{"code":"TOO_MANY_REQUESTS",...}}` y `Retry-After: 6` |
| 502 | Conexión rechazada o servicio sin direcciones (`no live upstreams`) | `{"detail":{"code":"UPSTREAM_UNAVAILABLE",...}}` |
| 504 | Timeout de conexión (2 s) o de lectura (30 s) | `{"detail":{"code":"UPSTREAM_TIMEOUT",...}}` |

Los errores que devuelven los servicios (por ejemplo 409 o 422 de FastAPI) pasan sin cambios:
`proxy_intercept_errors` está desactivado.

## Descubrimiento DNS y escalado sin reiniciar Nginx

Con `server product-service:8000 resolve`, Nginx consulta al DNS de Docker (`127.0.0.11`) y
vuelve a resolver el nombre cada 10 segundos (`valid=10s`). Al ejecutar
`docker compose up -d --scale product-service=3`, el DNS devuelve tres direcciones y Nginx las
reparte por turnos; al reducir a una réplica, las direcciones eliminadas dejan de usarse. Nginx
no se reinicia ni se recarga. Si el servicio no tiene contenedores, el DNS responde `NXDOMAIN`,
Nginx elimina la dirección anterior (`could not be resolved (3: Host not found)`) y la ruta
responde 502 (`no live upstreams`); el gateway sigue funcionando para las demás rutas. Cuando el
servicio vuelve, la siguiente resolución añade su dirección. Para que esto funcione, Nginx debe
esperar la respuesta del DNS lo suficiente (`resolver_timeout 5s`, hallazgo H-02).

product-service no tiene `container_name` ni publica puerto en el anfitrión; el puerto 8002 se
retiró de `compose.override.example.yaml` porque varias réplicas no pueden compartirlo. La
cabecera `X-Instance` de product-service es el hostname del contenedor (su id corto), distinto
en cada réplica.

## Procedimiento

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d --build
docker compose -f compose.yaml -f compose.override.example.yaml ps
backend\order-service\.venv\Scripts\python.exe -m pytest tests\gateway -v -s
```

## Resultados

| # | Verificación | Prueba | Resultado esperado | Estado |
|---|---|---|---|---|
| 1 | Enrutamiento de todas las rutas públicas | `test_2_routing.py::test_1_*` | 25 llamadas llegan al servicio y endpoint correctos (contenido propio del endpoint y `upstream_addr` del servicio en el log); 15 rutas desconocidas dan 404 sin upstream; redirección conserva `/api`; errores de FastAPI sin cambios; 413 sobre 1 MB | Aprobada |
| 2 | Compra completa por el gateway | `test_3_purchase.py` | Pedido 201, repetición con la misma clave 200 con el mismo id, pago APPROVE 200 con `PAID`/`CONFIRMED`, stock (3, 0), carrito vacío, pago APPROVED, notificación ORDER_PAID marcada como leída, operaciones DONE | Aprobada |
| 3 | Bloqueo de `/internal` y evasiones | `test_4_internal.py` | 23 variantes dan 404 con el mismo cuerpo, sin `X-Instance` y con `upstream_addr` vacío; desde order-service sin clave, `/internal` responde 401/403 | Aprobada |
| 4 | Escalado a tres réplicas | `test_5_scaling.py::test_4_*` | Tres valores de `X-Instance` iguales a los hostnames de las réplicas; mismo contenedor de gateway, misma hora de inicio y mismos reinicios | Aprobada |
| 5 | Reducción a una réplica | `test_5_scaling.py::test_5_*` | Solo la réplica restante responde y el log solo muestra su dirección | Aprobada |
| 6 | Una réplica detenida | `test_5_scaling.py::test_6_*` | 30 solicitudes sin errores, atendidas por las otras dos | Aprobada |
| 7 | Aislamiento de redes | `test_1_config.py::test_7_*` | Gateway solo en `ecommerce-net`; sin variables de base, claves ni secretos; alcanza product-service, `postgres` ni siquiera resuelve (`bad address`) | Aprobada |
| 8 | Servicio caído | `test_6_failures.py::test_8_*` | Disponible al inicio; tras detenerlo, el DNS responde NXDOMAIN en menos de 5 s (medido dentro del contenedor); respuestas JSON (502, o 504 por timeout de conexión mientras se usa la IP anterior); el log pasa a `upstream_addr = notification_backend` con `no live upstreams`; durante 15 s ninguna conexión a la IP antigua; recuperación y nuevo enrutamiento con el mismo contenedor de Nginx. Conexión rechazada: 502 con `Connection refused` | Aprobada |
| 9 | Timeout | `test_6_failures.py::test_9_*` | Contenedor pausado: 504 JSON `UPSTREAM_TIMEOUT` a los ~30 s, `upstream_connect_time` numérico (sí conectó), `timed out ... reading response header`, un solo intento; recuperación sin reiniciar Nginx | Aprobada |
| 10 | `X-Request-ID` | `test_2_routing.py::test_10_*`, `test_7_headers.py` | Generado si falta o es inválido, conservado si es válido, presente en el log, recibido por el servicio y devuelto al cliente; el log no contiene tokens ni contraseñas | Aprobada |
| 11 | `X-Internal-Key` | `test_7_headers.py`, `test_4_internal.py` | No llega al servicio aunque el cliente la envíe | Aprobada |
| 12 | Sin versión de Nginx | `test_1_config.py::test_12_*` | `Server: nginx` en 200, 404 y en la página HTML de error 400 | Aprobada |
| 13 | Límite de login | `test_8_rate_limit.py` | 6 intentos llegan a user-service (401), los 6 siguientes reciben 429 JSON sin upstream; otras rutas y el registro no se limitan; tras 6 s se admite otro intento | Aprobada |
| 14 | `Authorization` e `Idempotency-Key` | `test_2_routing.py::test_14_*`, `test_7_headers.py` | Llegan sin cambios; el mismo ajuste de inventario repetido se aplica una sola vez | Aprobada |
| 15 | `docker compose config --quiet` y `nginx -t` | `test_1_config.py::test_15_*` | Ambos correctos; solo el gateway publica puerto, en 127.0.0.1 | Aprobada |
| 16 | Estado saludable | `test_1_config.py::test_16_*` | Gateway, seis servicios y order-worker `healthy`; sistema de archivos del gateway de solo lectura | Aprobada |

**Resultado observado** (después de la corrección H-02, `GATEWAY_PORT=8090`):
```text
docker compose ... config --quiet                -> sin salida (correcto)
docker compose ... exec gateway nginx -t         -> syntax is ok / test is successful
docker compose ... exec gateway grep resolver    -> resolver 127.0.0.11 valid=10s ipv6=off;
                                                    resolver_timeout 5s;
pytest tests\gateway -v -s                       -> 25 passed in 192.38s
pytest tests\gateway\test_6_failures.py -v -s    -> 3 passed in 95.27s (con la medición DNS corregida)
pytest tests\e2e -v                              -> 12 passed in 289.86s (regresión)
```

Evidencias registradas por las pruebas:

| Verificación | Salida real |
|---|---|
| 4 Escalado a 3 réplicas | `{'8b315dfdd5c0': 10, '3a7f0091a00a': 10, '12437b45e63b': 10}` |
| 6 Una réplica detenida | `{'8b315dfdd5c0': 15, '3a7f0091a00a': 15}` |
| 5 Vuelta a 1 réplica | `upstream {'172.20.0.8'}` |
| 13 Límite de login | `[401, 401, 401, 401, 401, 401, 429, 429, 429, 429, 429, 429]` |
| 10, 11, 14 Cabeceras recibidas por el servicio | `authorization: Bearer header.payload.signature`, `idempotency-key: gwt-idem-123`, `x-internal-key: null`, `x-request-id: gwt-headers-ec19514373e0`, `x-forwarded-for: 172.20.0.1` (el cliente envió `203.0.113.66`) |
| 9 Servicio pausado | `504 after 30.0 s: upstream_addr=172.20.0.6:8000 upstream_status=504 connect_time=0.001` |

Prueba 8, servicio detenido (ejecución con la medición corregida):
```text
DNS after stop: inside the container 2540 ms, whole docker compose exec 2766 ms
** server can't find notification-service: NXDOMAIN
during the change (status, upstream_addr, connect_time):
   (504, '172.20.0.6:8000', '-')
   (504, '172.20.0.6:8000', '-')
   (502, 'notification_backend', '-')
after the update: 30 requests, upstream_addr=notification_backend, 0 error-log lines with 172.20.0.6:8000
recovered: upstream_addr=172.20.0.6:8000 (before the stop 172.20.0.6:8000)
refused: status=502 upstream_addr=172.20.0.6:8000 upstream_status=502
```
Lectura: mientras Nginx conserva la IP anterior en su caché (hasta `valid=10s`) la conexión
expira y se responde 504; tras la siguiente resolución recibe `NXDOMAIN`
(`could not be resolved (3: Host not found)` en el error log), elimina la dirección y responde
502 (`no live upstreams`, `upstream_addr = notification_backend`). En los 15 s siguientes no hubo
ninguna conexión a la IP antigua. Al volver el servicio, Docker le asignó la misma IP y el gateway
volvió a enrutarlo; el contenedor de Nginx fue el mismo (id, hora de inicio y reinicios).

## Hallazgos

### H-01 Puerto 8080 ocupado en el equipo de desarrollo

**Síntoma:** `docker compose up` falló con `ports are not available: ... 127.0.0.1:8080 ... bind:
An attempt was made to access a socket in a way forbidden by its access permissions`, y
`http://127.0.0.1:8080` respondía `Server: Embedthis-http`.

**Diagnóstico:** `Get-NetTCPConnection -LocalPort 8080` mostró el proceso `ApplicationWebServer`
(PID 11420) escuchando en `0.0.0.0:8080`, ajeno al proyecto.

**Solución:** sin cambiar código, `GATEWAY_PORT=8090` en `.env` (el 8090 estaba libre). Las
pruebas leen el mismo valor.

### H-02 Nginx conservaba la IP de un servicio detenido

**Síntoma:** en la segunda ejecución de la suite, tras `docker compose stop notification-service`,
el gateway respondió durante más de 30 s `504 UPSTREAM_TIMEOUT` (en 2 s) en lugar de 502, y la
prueba 8 falló. En la primera ejecución la misma prueba había pasado.

**Diagnóstico** (log real del gateway):
```text
[error] notification-service could not be resolved (110: Operation timed out)
[error] upstream timed out (110: Operation timed out) while connecting to upstream,
        upstream: "http://172.20.0.3:8000/notifications"
{"path":"/api/notifications","status":504,"request_time":2.002,"upstream_addr":"172.20.0.3:8000",
 "upstream_status":"504","upstream_connect_time":"-"}
```
- Con el servicio sin contenedores, el DNS de Docker no responde de inmediato: consulta antes al
  DNS del equipo y luego contesta `NXDOMAIN`.
- Con `resolver_timeout 2s`, Nginx abandonaba la consulta antes de la respuesta. Ante un error de
  resolución (distinto de `NXDOMAIN`) Nginx conserva las direcciones anteriores, por lo que seguía
  intentando `172.20.0.3`, donde ya no había nada (timeout de conexión, 504).
- Riesgo asociado: si Docker asignaba esa IP a otro contenedor, el tráfico de esa ruta llegaría a
  él. En la prueba 7 el servidor de eco recibió precisamente `172.20.0.3`.
- El fallo de la prueba 9 en esa ejecución fue consecuencia del anterior: empezó cuando
  notification-service acababa de arrancar (`Connection refused`, 502 en 0,0 s), porque el
  `finally` de la prueba 8 no esperaba a que el servicio estuviera listo.

**Medición:** con el servicio detenido,
`Measure-Command { docker compose ... exec gateway nslookup -timeout=20 -retry=1 notification-service 127.0.0.11 }`
devolvió `NXDOMAIN` (dos consultas) en 2,86 s. Ese tiempo incluye el arranque de
`docker compose exec`, por lo que no se atribuye entero al DNS; la prueba 8 mide además la
resolución desde dentro del contenedor ya iniciado.

**Configuración anterior:** `resolver_timeout 2s;`
**Configuración corregida:** `resolver_timeout 5s;` (sin otros cambios: `resolver 127.0.0.11
valid=10s ipv6=off` y los upstreams con `resolve` se mantienen; la clasificación 502/504 es la
predeterminada de Nginx).

**Resultados antes de la corrección:**

| Ejecución | Resultado |
|---|---|
| 1 | 23 passed, 1 failed (`test_1_every_public_route...`: la prueba llamaba a `GET /inventory` sin el parámetro obligatorio `ids`; el 422 lo devolvió inventory-service, es decir, la ruta sí llegaba. Corregida la prueba). Pruebas 8 y 9 aprobadas (504 a los 30,0 s) |
| 2 | 22 passed, 2 failed (pruebas 8 y 9, descritas arriba) |

**Medición dentro del contenedor:** la prueba 8 mide `nslookup -type=a` (solo registro A, como
Nginx con `ipv6=off`) desde el gateway ya iniciado, con `/proc/uptime` (resolución de 10 ms):
**2540 ms** hasta `NXDOMAIN` (frente a 2766 ms de toda la llamada `docker compose exec`). El valor
supera los 2 s del límite anterior, lo que explica el fallo, y queda por debajo de los 5 s actuales.

Nota sobre la medición: en la primera ejecución tras la corrección, la prueba usaba
`date +%s%N`; el `date` de BusyBox no admite `%N`, por lo que el valor resultante (`ELAPSED_NS=3`,
en realidad unos 3 s con resolución de segundos) no era válido y la comprobación pasaba sin medir.
Se corrigió para usar `/proc/uptime` y para fallar si no obtiene la medición.

**Resultados después de la corrección:**

| Ejecución | Resultado |
|---|---|
| 3 | 25 passed (suite completa; la medición DNS de esa ejecución no era válida, ver nota) |
| 4 | `test_6_failures.py`: 3 passed, con la medición corregida (2540 ms) |
| E2E | 12 passed (regresión con product-service accesible solo por el gateway) |

## Limitaciones

- La cabecera `X-Instance` (hostname del contenedor) llega al cliente; sirve para demostrar el
  escalado y no contiene datos sensibles.
- El límite de login es por dirección IP. Detrás de un NAT, varios usuarios comparten el límite.
- El gateway escucha HTTP en `127.0.0.1`; no se configura TLS en este entorno local.
- Se usa el resolver de Docker; fuera de Docker habría que cambiar la dirección del resolver.
- Cuando un servicio queda sin contenedores, el descarte de su IP depende de que el DNS de Docker
  responda `NXDOMAIN` en menos de 5 s (medido: 2,54 s en este equipo). Si tardara más, Nginx
  conservaría la IP anterior y respondería 504 JSON hasta que el servicio vuelva. Hasta la
  siguiente resolución (como máximo 10 s) también se responde 504 por timeout de conexión.
