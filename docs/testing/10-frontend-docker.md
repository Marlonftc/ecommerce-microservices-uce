# Etapa 6: frontend en Docker, gateway, seguridad y pruebas E2E de interfaz

Fecha: 2026-10-23

Estado: **verificación con Docker realizada en el equipo del autor. Gateway 34/34 tras la corrección de `test_8`.
Vitest aún no es estable (H-07).** La etapa no se da por cerrada hasta resolverlo.

## Cambios

| Archivo | Cambio |
|---|---|
| `frontend/Dockerfile`, `frontend/.dockerignore` | Imagen multietapa: `node:22.23.3-alpine` compila (`npm ci`, `typecheck`, `build`); `nginxinc/nginx-unprivileged:1.28.0-alpine` sirve `dist/` como uid 101 en el puerto 8080. Etiqueta `uce.payment-demo` |
| `frontend/nginx/default.conf` | Navegación SPA (`try_files $uri /index.html`), `/assets/` con caché de un año y 404 real, `index.html` con `no-cache`, `/healthz`, gzip |
| `compose.yaml` | Servicio `frontend`: sin puertos, solo `ecommerce-net`, `read_only`, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, argumento `VITE_PAYMENT_DEMO` (por defecto `false`) |
| `compose.e2e.yaml` | Frontend con `VITE_PAYMENT_DEMO=true` en la imagen `ecommerce-uce/frontend:e2e` (no sobrescribe la normal) |
| `gateway/conf.d/gateway.conf` | Upstream `frontend`; `/` hacia el frontend (solo GET y HEAD); `/api` y `/api/` desconocidos en 404 JSON; cabeceras por archivo incluido |
| `gateway/snippets/security-headers.conf`, `gateway/Dockerfile` | `X-Request-ID`, `nosniff`, CSP, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP y CORP; incluidas en el servidor y en `@rate_limited` |
| `.env.example` | `VITE_PAYMENT_DEMO=false` documentada |
| `frontend/src/test/setup.ts`, `frontend/vite.config.ts` | Solo pruebas: `asyncUtilTimeout` de 3000 ms para `waitFor`/`findBy` (H-06) |
| `tests/gateway/` | `test_2_routing.py` (rutas fuera de `/api` servidas por el frontend), `test_1_config.py` (aislamiento del frontend), `test_6`/`test_8` (cabeceras en 502, 504 y 429; ventana de `test_8` corregida, H-05) y `test_9_frontend.py` (nuevo) |
| `tests/ui/` | Suite Playwright nueva (22 pruebas) |
| `README.md`, `frontend/README.md`, `tests/*/README.md`, `docs/architecture/decisiones.md` (D20, D21) | Documentación |

Ningún microservicio ni contrato de API cambió.

## Verificación local (sin Docker)

El entorno de desarrollo del asistente no puede descargar imágenes de Docker Hub, así que las imágenes
no se construyeron allí. Se verificó con los mismos archivos de configuración:

- Gateway: `nginx.conf`, `gateway.conf` y el archivo de cabeceras reales, solo con rutas y puerto
  adaptados (8088), en Nginx 1.29.8 (la imagen usa 1.28.0; las directivas usadas existen en ambas).
- Frontend: `frontend/nginx/default.conf` real, en otra instancia de Nginx (127.0.0.41:8080).
- Los seis microservicios y el worker reales con PostgreSQL 16, y un DNS local que reemplaza al de Docker.
- order-service y order-worker con los tiempos cortos de `compose.e2e.yaml`.

### L-01 Configuración

| Comprobación | Resultado |
|---|---|
| `nginx -t` del gateway y del frontend | `syntax is ok`, `test is successful` |
| `docker compose config --quiet` (compose.yaml y con override + e2e, con un `.env` de prueba) | Correcto |
| Puertos publicados por `compose.yaml` | Solo `gateway`, `127.0.0.1:8080:80` |
| Servicio `frontend` resuelto | Sin puertos, `ecommerce-net`, `read_only`, tmpfs `/tmp`, `cap_drop: [ALL]`, `no-new-privileges:true`, `VITE_PAYMENT_DEMO=false` |
| Con `compose.e2e.yaml` | Imagen `ecommerce-uce/frontend:e2e`, `VITE_PAYMENT_DEMO=true` |
| Variable `VITE_PAYMENT_DEMO=true` en la terminal | Compose la usa sobre `.env` (comportamiento documentado) |
| Pasos de la etapa de compilación en un contexto limpio según `.dockerignore` (sin pruebas ni `src/test`) | `npm ci`, `typecheck` y `build` correctos; mismo bundle `index-BRUzjXu5.js` que el build normal |
| Build normal / build E2E | `Opciones de demostración` aparece 0 / 1 veces en el JavaScript |

### L-02 Rutas a través del gateway

| Ruta | Resultado |
|---|---|
| `/`, `/login`, `/carrito`, `/pedidos`, `/pedidos/abc`, `/admin`, `/admin/usuarios`, `/notificaciones`, `/xyz` | 200 `text/html`, `Cache-Control: no-cache` |
| `/assets/index-*.js` | 200 `application/javascript`, `public, max-age=31536000, immutable` |
| `/assets/missing.js`, `/assets/` | 404 (nunca `index.html`) |
| `/api`, `/api/`, `/api/xyz`, `/api/PRODUCTS` | 404 JSON `NOT_FOUND`, sin reenviar |
| `/internal/x`, `/Internal/x`, `/api/products/%2e%2e/internal` | 404 JSON, sin reenviar |
| `/API/products`, `/docs`, `/health/ready` | 200 `index.html` del frontend (nunca llegan a un microservicio) |
| `POST /login` | 405 JSON `METHOD_NOT_ALLOWED`, sin reenviar |
| `/health`, `/api/products` | 200 JSON, como antes |

### L-03 Cabeceras en cada tipo de respuesta

`X-Request-ID`, `X-Content-Type-Options`, `Content-Security-Policy` (una sola copia), `X-Frame-Options`,
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy` y `Cross-Origin-Resource-Policy`
presentes en: HTML, asset, API 200, API 404, `/internal` 404, 405, 413, 429 (12 intentos de login:
6 × 401 y 6 × 429, con `Retry-After: 6`), 502 (frontend detenido) y 504 (notification-service congelado,
respuesta a los 30 s). Un `X-Request-ID` enviado por el cliente se devuelve igual; el log del gateway
registra el upstream del frontend.

### L-04 Disponibilidad

| Escenario | Resultado |
|---|---|
| Frontend detenido | `/login` 502 JSON `UPSTREAM_UNAVAILABLE` con cabeceras; `/api/products` 200; `/api/auth/register` responde |
| Gateway reiniciado con el frontend detenido | Arranca; `/api/products` 200; `/` 502 |
| Frontend de vuelta | `/carrito` 200 |

### L-05 Pruebas del gateway (pytest) ejecutables sin Docker

Se ejecutaron contra el gateway local, leyendo su log en lugar de `docker compose logs`:
`test_2_routing.py` y `test_9_frontend.py` sin las pruebas que requieren Docker (13 passed),
`test_4_internal.py` (las 23 variantes de `/internal`: passed) y `test_8_rate_limit.py` (passed).
Requieren Docker y quedan para el equipo del autor: `test_10_log_has_no_tokens_or_passwords`,
`test_normal_image_is_built_without_payment_demo`,
`test_api_works_with_the_frontend_stopped_and_after_a_gateway_restart`, `test_3_second_barrier_inside_the_network`,
y los archivos `test_1`, `test_3`, `test_5`, `test_6` y `test_7`.

### L-06 Suite Playwright

Contra el gateway local con el build E2E; los servicios se detienen y arrancan con un comando local
(`UI_SERVICE_CONTROL`) en lugar de Docker Compose.

```text
22 passed (3.2m)
[ui6 cleanup] run …: 11 products and 14 accounts deactivated
```

- Se ejecutó completa varias veces; cada ejecución crea sus propios datos y no depende de las anteriores.
- Cero violaciones de CSP y cero errores no capturados en todas las páginas visitadas. La prueba de control
  inyecta un script en línea y comprueba que la CSP lo bloquea y que la vigilancia lo detecta.
- El reporte HTML se revisó descomprimido: sin tokens JWT, sin `Bearer`, sin la contraseña del
  administrador y sin contraseñas de clientes (ver H-02).

### Hallazgos

- **H-01** (prueba): el texto accesible de la campana con notification-service caído es
  «Notificaciones (no se pudo consultar cuántas hay sin leer)». La prueba buscaba otro texto; la
  aplicación era correcta y se corrigió la prueba.
- **H-02** (seguridad de reportes): `locator.fill()` escribe el valor en el título del paso del reporte HTML,
  por lo que las contraseñas aleatorias de los clientes de prueba aparecían en él. Las contraseñas se
  escriben ahora con `fillSecret()` (setter nativo + evento `input`), que no las registra. Revisión
  repetida: sin contraseñas en el reporte.
- **H-03** (entorno local): el comando local de arranque de servicios dejaba abierta la salida estándar y
  bloqueaba la prueba; se corrigió en el script local (no forma parte del repositorio).

## Procedimiento con Docker (equipo del autor)

Los comandos y criterios están en la respuesta de entrega de la etapa 6 y en el README. Resumen:

1. `docker compose config --quiet`, `docker compose build`, `docker compose up -d`, `docker compose ps -a`.
2. Usuario, red, solo lectura y tamaño de la imagen del frontend; etiqueta `uce.payment-demo=false`.
3. `pytest tests\gateway -v` con `compose.yaml` + `compose.override.example.yaml`.
4. Sistema con `compose.e2e.yaml`; `pytest tests\e2e -v`; `npx playwright test` en `tests\ui`.
5. Restaurar el frontend normal y comprobarlo (etiqueta y bundle servido).

## Resultados con Docker

Equipo del autor: Windows, Docker Desktop, PowerShell, `GATEWAY_PORT=8090`. Fecha: 2026-10-23.

| Comprobación | Resultado |
|---|---|
| Construcción | Correcta con `node:22.23.3-alpine` y `nginxinc/nginx-unprivileged:1.28.0-alpine`; la imagen E2E se etiquetó `ecommerce-uce/frontend:e2e` y compiló con `VITE_PAYMENT_DEMO="true"` |
| Usuario del frontend | `uid=101(nginx) gid=101(nginx)` |
| Aislamiento (`test_7_frontend_isolated_unprivileged_and_read_only`) | Passed: solo `ecommerce-net`, solo lectura, `cap_drop: ALL`, `no-new-privileges`, sin puertos, PostgreSQL no resuelve |
| Gateway (`pytest tests\gateway`) | **33/34** antes de la corrección (falla `test_8_stopped_service_dns_update_and_recovery`, H-05). **Después: `test_8` 3/3 en ejecuciones consecutivas y la suite completa 34/34** (245 s). `test_9_frontend.py`: 7/7 |
| E2E de la API (`pytest tests\e2e`) | **12/12** (283 s). Un primer intento salió sin ejecutar pruebas porque se lanzó con los servicios aún en `health: starting` |
| E2E de interfaz (Playwright, con `compose.e2e.yaml`) | **22/22** (5,5 min); limpieza: 11 productos y 14 cuentas desactivados |
| Vitest | Antes del ajuste: 368/368 en la ejecución satisfactoria y 367/368 en otra (H-06). **Después: 5 de 6 ejecuciones 368/368** (3/3 sin `VITE_PAYMENT_DEMO`, 2/3 con `VITE_PAYMENT_DEMO=true`); una dio 367/368 sin el nombre de la prueba en la salida filtrada (H-07). `typecheck` correcto y mismo bundle |
| Build del frontend | Mismo bundle `index-BRUzjXu5.js` que en el entorno de desarrollo |
| Restauración tras las E2E | Contenedor con `ecommerce-uce/frontend`; etiqueta `"uce.payment-demo":"false"`; el bundle servido por el gateway no contiene «Opciones de demostración» (`False`) |

Tamaños de imagen (`docker image ls`, tamaño en disco / contenido comprimido):

| Imagen | Disco | Contenido |
|---|---|---|
| `ecommerce-uce/frontend:latest` | 74,3 MB | 21,1 MB |
| `ecommerce-uce/frontend:e2e` | 74,3 MB | 21,1 MB |
| `ecommerce-uce/gateway` | 73,7 MB | 21 MB |
| `ecommerce-uce/user-service` | 320 MB | 74,7 MB |
| `ecommerce-uce/inventory-service`, `order-service` | 317 MB | 74,1 MB |
| `ecommerce-uce/product-service`, `payment-service`, `notification-service` | 315 MB | 73,6 MB |

### Hallazgos con Docker

- **H-04 (documentación):** el comando `docker image inspect … --format '{{ index .Config.Labels "uce.payment-demo" }}'`
  falla en Windows PowerShell (`function "uce" not defined`): PowerShell elimina las comillas dobles internas al
  pasar el argumento a `docker`. Se reemplazó por `--format '{{json .Config.Labels}}'` en `README.md`,
  `frontend/README.md` y el comentario de `frontend/Dockerfile`. La imagen no cambió. La etiqueta ya se había
  comprobado también en `test_normal_image_is_built_without_payment_demo`.
- **H-05 (prueba del gateway):** `test_8` falló dos veces seguidas con la misma causa. Una petición enviada justo
  antes de que Nginx descartara la IP vieja registró su «upstream timed out … 172.20.0.x:8000» un instante antes
  de la primera respuesta ya actualizada. La ventana «después del cambio» se abría con `support.utc_now()`, que
  **resta 1 s y trunca a segundos**: medido con `docker compose logs --since`, incluía hasta 1,7 s de líneas
  anteriores al instante tomado (17 líneas frente a 8 con un instante con fracciones). En las dos ejecuciones,
  las aserciones previas sí pasaron: todas las peticiones de la ventana de 15 s tuvieron
  `upstream_addr=notification_backend` y estado 502.
  **Corrección:** la ventana ahora empieza en la línea del log de acceso de la **primera petición ya actualizada**,
  y las líneas se ordenan por las marcas de tiempo del propio Docker (`--timestamps`, nanosegundos, mismo reloj), sin
  usar el reloj del PC ni redondear a segundos. No se agregó ninguna espera. Se mantienen todas las aserciones
  originales y se añadió una: el log posterior al cambio no puede estar vacío. Verificado con un contenedor que
  simula el log del gateway: la línea de la petición anterior queda fuera y las posteriores dentro.
  **Resultado en el equipo del autor:** `test_8` pasó 3 veces seguidas y la suite completa pasó 34/34.
- **H-06 (Vitest):** `waitFor`/`findBy` esperaban como máximo 1 s. La primera prueba de cada archivo también paga la
  carga de los módulos de la aplicación y, con muchos archivos en paralelo, algunas esperas correctas tardaron
  1,1-1,2 s (carrito, campana, checkout). Reproducido en el entorno de desarrollo con 8 procesos sobre 2 núcleos:
  3 de 3 ejecuciones fallaron (3-4 pruebas cada una, siempre la primera de su archivo y siempre por espera agotada).
  **Corrección:** `src/test/setup.ts` fija `asyncUtilTimeout` en 3000 ms (cargado con `setupFiles` en
  `vite.config.ts`). Sin pausas fijas y sin cambios en el código de producción; el bundle no cambia.
  Después: con la misma carga, 3 de 3 ejecuciones y una con orden aleatorio, 368/368; con el paralelismo normal,
  3 ejecuciones sin `VITE_PAYMENT_DEMO` y 3 con `VITE_PAYMENT_DEMO=true`, todas 368/368. No se redujo el
  paralelismo: con el normal ya no falla.
  **Límite de la evidencia:** con 12 procesos sobre 2 núcleos siguen fallando 5 pruebas, 3 de ellas por el límite de
  5 s de cada prueba; esa sobrecarga es muy superior a la de una ejecución normal y no se ocultó con tiempos mayores.
  Si volviera a ocurrir en un equipo concreto, la opción es `npx vitest run --maxWorkers=50%`.
  **Resultado en el equipo del autor:** 5 de 6 ejecuciones 368/368; ver H-07.
- **H-07 (Vitest, abierto):** tras el ajuste de H-06 sigue habiendo fallos ocasionales que **no son esperas agotadas**.
  En el entorno de desarrollo, con el ajuste aplicado: 19 ejecuciones completas con el paralelismo normal (3 sin y 16 con
  `VITE_PAYMENT_DEMO=true`), 18 con 368/368 y 1 con 367/368; con 8 procesos sobre 2 núcleos, 1 de 7 ejecuciones falló por
  el límite de 5 s de una prueba (`catalog-flow`, primera del archivo). La falla con paralelismo normal fue
  `cart-flow › updates a quantity with PUT…`, con «Unable to find … button "Actualizar"» buscado de forma inmediata
  justo después de escribir la cantidad. Hipótesis: cuando `findBy` termina, el efecto de montaje de la línea del
  carrito (`setDraft(item.quantity)`) puede seguir pendiente; React lo aplica después de lo escrito y la cantidad vuelve
  al valor original, por lo que el botón no aparece. No depende del límite de 3 s y no afecta a un usuario real (el
  intervalo es de milisegundos), pero hace que la prueba dependa del orden de tareas del motor. No se pudo identificar
  la prueba que falló en el equipo del autor porque la salida estaba filtrada. Corrección pendiente de autorización.
