# Pruebas de carga

Fecha: 2026-10-22 (00:27 a 00:51, hora de Ecuador)
Herramienta: k6 1.0.0 en contenedor (`grafana/k6:1.0.0`, `compose.load.yaml`), conectado a
`ecommerce-net`; todas las solicitudes van al gateway (`http://gateway`).
Entorno: un único portátil con Windows y Docker Desktop. CPU, memoria, Docker y PostgreSQL son
compartidos por todos los contenedores y por el propio k6. Los resultados describen este entorno
y no se extrapolan a producción.

## Escenarios

| # | Script | Carga | Qué mide |
|---|---|---|---|
| 1 | `browse.js` | Usuarios virtuales: 0 → 10 (30 s) → 20 (60 s) → 0 (30 s); 1 s de pausa entre visitas | Listado, detalle y existencias: req/s, latencia media y p95, errores, comprobaciones |
| 2 | `purchase.js` | 4 compradores concurrentes durante 2 min, cada uno con su cliente y token | Carrito, checkout, pago APPROVE y consulta hasta `PAID`/`CONFIRMED`; latencia por operación y tiempo total de compra |
| 3 | `mixed.js` | Tasa fija durante 2 min: 3 visitas/s de navegación y 30 compras/min | Proporción objetivo ~80 % / 20 % de **solicitudes HTTP públicas**; demanda por servicio en el log del gateway |
| 4 | `scale.js` | product-service con 1 y con 3 réplicas, 3 repeticiones cada una: calentamiento 20 s a 10 it/s, tasa sostenida 60 s a 40 it/s, rampa 0 → 40 usuarios en 60 s y 30 s estable | Rendimiento, p95, errores, reparto entre réplicas, CPU y memoria |

Cada iteración de navegación son 3 solicitudes (listado, detalle, existencias); cada iteración de
`scale.js` son 2 (listado y detalle).

### Preparación de datos

- `setup()` usa solo rutas públicas y de administrador del gateway: `POST /api/products` y
  `POST /api/inventory/{id}/adjustments` (con `Idempotency-Key`), comprobando su respuesta. No se
  usa ninguna ruta `/internal`.
- Cada ejecución crea 5 productos `LOAD-<id de ejecución>` con stock amplio y los desactiva al
  terminar (`DELETE /api/products/{id}`). Los datos existentes no se modifican.
- Cada comprador crea su propio cliente en su primera iteración (registro e inicio de sesión), antes
  de medir compras. Motivo: k6 no da a cada usuario virtual un número estable dentro de su escenario,
  por lo que un grupo de clientes preparado en `setup()` podría asignar el mismo cliente a dos
  compradores concurrentes. Esas solicitudes se etiquetan como preparación y no entran en los umbrales.
- Inicio de sesión: si el gateway responde 429 (límite de 10/min), k6 espera `Retry-After` y repite.
  `run_load.py` espera 40 s entre repeticiones que inician sesión.
- Sin contraseñas ni JWT en el código: el administrador llega desde `.env`; las contraseñas de los
  clientes son aleatorias y solo existen en memoria; `handleSummary()` elimina `setup_data` (que
  contiene tokens) antes de guardar el resumen.

### Compras

- Claves de idempotencia únicas por checkout y por pago; si la respuesta es un error de red o
  502/503/504, se repite la misma solicitud con la misma clave (máximo 2 veces).
- `202` en checkout (`RESERVING`) o en pago (`PAYMENT_PENDING`) es una respuesta válida: el script
  consulta el pedido hasta su estado final. No se usa `SLOW_APPROVE`.
- Un comprador no inicia otra compra mientras la anterior está activa; si una compra falla con un
  pedido pendiente, lo cancela.
- La compra cuenta como completada solo si el pedido llega a `PAID` con inventario `CONFIRMED`.

### Umbrales de referencia

| Escenario | Umbral |
|---|---|
| Lectura (listado, detalle, existencias, consulta de pedido) | p95 < 500 ms |
| Carrito | p95 < 1000 ms |
| Checkout y pago (llaman a varios servicios) | p95 < 3000 ms |
| Tiempo total de una compra | p95 < 6000 ms |
| Compras correctas | > 99 % |
| Errores HTTP inesperados (fase principal) | < 1 % |
| Comprobaciones funcionales | > 99 % |

Son objetivos iniciales, no requisitos del enunciado. Un incumplimiento se informa tal cual.

### Decisión de diseño del escenario 4

Durante la preparación, en un entorno de validación sin Docker (servicios con uvicorn y Nginx
locales, 2 CPU), una rampa de **modelo abierto** (k6 envía a una tasa fija aunque el servidor no
responda) hasta 600 req/s superó la capacidad de una réplica de product-service: se agotó el pool
de conexiones a la base (`QueuePool limit of size 5 overflow 5 reached`), el gateway respondió 504
y el servicio siguió procesando la cola de solicitudes abandonadas durante unos 5 minutos antes de
volver a responder. Una medición así contamina la siguiente. Por eso la rampa usa un **modelo
cerrado** (cada usuario virtual espera su respuesta; como máximo 40 solicitudes en curso) y
`run_load.py` comprueba antes de cada ejecución que el catálogo vuelve a responder en menos de 1 s.

## Procedimiento

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py browse
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py purchase
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py mixed
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py scale --replicas 1 --repeat 3
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py scale --replicas 3 --repeat 3
```

## Entorno medido

| Elemento | Valor |
|---|---|
| k6 | `k6 v1.0.0` (imagen `grafana/k6:1.0.0`) |
| Docker | Docker Desktop, servidor 29.9.1 |
| Recursos asignados a Docker | 12 CPU, 7,7 GiB de memoria |
| Gateway | `http://gateway` dentro de `ecommerce-net` (en el equipo, `GATEWAY_PORT=8090`) |

Comandos ejecutados: los del procedimiento, más
`docker compose -f compose.yaml -f compose.override.example.yaml -f compose.load.yaml config --quiet`
(sin salida) al inicio y `docker compose ... up -d --no-deps --scale product-service=1 product-service`
al final. Los 27 archivos de `tests/load/results/` (resumen de k6, salida y mediciones de entorno de
cada ejecución) se revisaron antes del commit: no contienen JWT, cabeceras `Authorization`,
contraseñas, la clave interna, el correo del administrador, otros correos ni direcciones IP.

### Ejecuciones registradas

| # | Identificador (`tests/load/results/`) | Escenario | Réplicas | Duración k6 | Código de salida |
|---|---|---|---|---|---|
| 1 | `browse-r1-20261022-002700` | Navegación | 1 | 121,1 s | 0 |
| 2 | `purchase-r1-20261022-002907` | Compra | 1 | 121,6 s | 0 |
| 3 | `mixed-r1-20261022-003114` | Mixto | 1 | 120,7 s | 0 |
| 4 | `scale-r1-20261022-003320` | Escalado | 1 | 170,2 s | 0 |
| 5 | `scale-r1-20261022-003623` | Escalado | 1 | 170,2 s | 0 |
| 6 | `scale-r1-20261022-003927` | Escalado | 1 | 170,2 s | 0 |
| 7 | `scale-r3-20261022-004232` | Escalado | 3 | 170,2 s | 0 |
| 8 | `scale-r3-20261022-004538` | Escalado | 3 | 170,2 s | 0 |
| 9 | `scale-r3-20261022-004843` | Escalado | 3 | 170,2 s | 0 |

Código 0: k6 terminó y se cumplieron todos los umbrales. Los valores de este documento se tomaron
de esos JSON.

## Resultados

### Escenario 1: navegación (`browse-r1-20261022-002700`)

| Métrica | Listado | Detalle | Existencias | Total |
|---|---|---|---|---|
| Latencia media | 7,7 ms | 5,3 ms | 5,1 ms | 6,2 ms |
| p95 | 10,3 ms | 9,2 ms | 6,8 ms | 9,3 ms |
| Máximo | 328,8 ms | 345,9 ms | 292,3 ms | 345,9 ms |

- 3935 solicitudes en 2 min (32,5 req/s, con 1 s de pausa entre visitas), 0,00 % de errores,
  100 % de comprobaciones correctas. Umbral p95 < 500 ms: cumplido.
- Gateway: product-service 2623 (66,7 %), inventory-service 1311 (33,3 %), user-service 1 (el inicio
  de sesión de preparación).

### Escenario 2: compra (`purchase-r1-20261022-002907`)

| Operación | Media | p95 | Máximo | Umbral |
|---|---|---|---|---|
| Carrito (`PUT /api/cart/items/{id}`) | 56,5 ms | 85,3 ms | 349,3 ms | p95 < 1000 ms ✔ |
| Checkout (`POST /api/orders`) | 80,7 ms | 137,9 ms | 273,8 ms | p95 < 3000 ms ✔ |
| Pago APPROVE | 169,9 ms | 462,3 ms | 786,5 ms | p95 < 3000 ms ✔ |
| Consulta del pedido | 8,3 ms | 17,5 ms | 26,0 ms | p95 < 500 ms ✔ |
| **Compra completa** | **316 ms** | **695 ms** | | p95 < 6000 ms ✔ |

- **364 compras completadas, 0 fallidas** (todas llegaron a `PAID` con inventario `CONFIRMED`), con
  4 compradores durante 2 min. 1481 solicitudes, 0,00 % de errores.
- Observado: el pago es la operación más lenta. Interpretación: dentro de la misma solicitud
  order-service llama a payment-service, confirma el inventario y registra la notificación.
- CPU media de order-service: 39,7 % (máximo 70,6 %), la más alta del escenario.

### Escenario 3: carga mixta (`mixed-r1-20261022-003114`)

| Proporción (solicitudes HTTP públicas) | Navegación | Compra |
|---|---|---|
| Objetivo | 80 % | 20 % |
| Conseguida (contador de k6) | 81,6 % (1080) | 18,4 % (244) |

Demanda pública por servicio, contada en el log del gateway (1349 solicitudes):

| Servicio | Solicitudes | % |
|---|---|---|
| product-service | 731 | 54,2 % |
| inventory-service | 365 | 27,1 % |
| order-service | 244 | 18,1 % |
| user-service | 9 | 0,7 % |

- user-service: registro e inicio de sesión de los 4 compradores y del administrador (preparación).
- 61 compras completadas, 0 fallidas; compra completa p95 227 ms. Lectura p95 entre 5,1 y 7,0 ms.
  0,00 % de errores, todos los umbrales cumplidos.
- Observado: con esta mezcla de uso, **product-service recibió la mayor demanda pública** (54,2 %).
- Inferido: order-service consulta a product-service en cada carrito y checkout; esas llamadas
  internas no pasan por el gateway y no están contadas, por lo que la demanda total de
  product-service sería todavía mayor.

### Escenario 4: escalado de product-service

Misma carga en las 6 ejecuciones (calentamiento 20 s, sostenida 60 s a 40 it/s = 80 req/s, rampa
0 → 40 usuarios en 60 s + 30 s estable), mismos endpoints y mismos datos, sin reiniciar Nginx.

| Réplicas | Ejecución (hora) | Sostenida: req/s | Sostenida: p95 | Rampa: req/s | Rampa: media | Rampa: p95 | Errores |
|---|---|---|---|---|---|---|---|
| 1 | 003320 | 80,0 | 6,2 ms | 195,5 | 134,9 ms | 246,9 ms | 0,00 % |
| 1 | 003623 | 80,0 | 6,1 ms | 194,8 | 135,4 ms | 248,0 ms | 0,00 % |
| 1 | 003927 | 80,0 | 5,9 ms | 193,9 | 136,0 ms | 253,3 ms | 0,00 % |
| 3 | 004232 | 80,0 | 6,1 ms | 444,2 | 59,2 ms | 197,8 ms | 0,00 % |
| 3 | 004538 | 80,0 | 6,3 ms | 441,6 | 59,5 ms | 262,2 ms | 0,00 % |
| 3 | 004843 | 80,0 | 6,2 ms | 443,7 | 59,2 ms | 252,3 ms | 0,00 % |

`Rampa: req/s` = solicitudes de la fase de rampa ÷ 90 s (60 s de subida + 30 s estable).

**Resultados observados** (promedio de 3 ejecuciones por configuración):

| Métrica | 1 réplica | 3 réplicas | Diferencia |
|---|---|---|---|
| Rampa: rendimiento | 194,7 req/s | 443,2 req/s | **+127,6 %** (×2,28) |
| Rampa: latencia media | 135,4 ms | 59,3 ms | **−56,2 %** |
| Rampa: p95 | 249,4 ms | 237,4 ms | −4,8 % |
| Sostenida (80 req/s): p95 | 6,1 ms | 6,2 ms | sin diferencia apreciable |
| Errores HTTP | 0,00 % | 0,00 % | |
| `dropped_iterations` en la fase sostenida | 0 | 0 | |

Reparto entre réplicas (log del gateway), por ejecución con 3 réplicas:
15061 / 15061 / 15061, 14985 / 14984 / 14984 y 15045 / 15045 / 15045.

Identificación de las réplicas: antes de cada ejecución `run_load.py` consultó el catálogo por el
gateway y obtuvo tres valores distintos de `X-Instance`: `0494ecbced0a`, `73ce001d98db` y
`8b315dfdd5c0` (el hostname de cada contenedor, que Docker toma de su id corto). Con una réplica
solo apareció `8b315dfdd5c0`. El reparto por réplica se contó con la dirección `upstream_addr` del
log del gateway, asociada a cada contenedor con `docker inspect`. Nginx no se reinició entre
ejecuciones.

Por qué el reparto es prácticamente exacto: Nginx usa por defecto *round robin* (turno rotativo)
con el mismo peso para las tres direcciones, y con `zone` el turno se comparte entre sus procesos;
cada solicitud va a la siguiente réplica. Como no hubo errores ni réplicas marcadas como caídas,
ningún turno se saltó, y la diferencia entre réplicas solo puede ser de una solicitud según dónde
termine la ejecución (14985 / 14984 / 14984).

CPU y memoria (`docker stats` cada ~3 s; media / máximo durante toda la ejecución, incluido el
calentamiento):

| Contenedor | 1 réplica | 3 réplicas |
|---|---|---|
| product-service (cada réplica) | 73–79 % / 151–155 % | 50–63 % / 123–163 % |
| postgres | 7–8 % / 22–28 % | 15–16 % / 32–39 % |
| gateway | ~4 % / 6–8 % | ~10 % / 19–21 % |
| k6 | ~8 % / 11–13 % | ~17 % / 30–31 % |
| Memoria de cada réplica | 92–99 MiB | 82–100 MiB |

**Conclusiones inferidas** (interpretación de los datos anteriores, no mediciones directas):
- Con una carga que una réplica atiende con holgura (80 req/s), añadir réplicas no cambia la
  latencia de forma apreciable.
- Con 40 usuarios concurrentes, una sola réplica limita el rendimiento: es el único contenedor
  cuya CPU llega a ~150 % (un proceso de Python), mientras PostgreSQL, el gateway y k6 se mantienen
  con CPU media ≤ 17 %. Por eso se atribuye la mejora de la rampa a las réplicas de product-service.
- El p95 de la rampa apenas cambia porque esa fase incluye la subida gradual, igual en ambas
  configuraciones; la latencia media refleja mejor el efecto.
- La mejora depende de que Docker disponía de 12 CPU; con menos núcleos sería menor.
- Estos números no son la capacidad máxima absoluta de product-service: la rampa se limitó a 40
  usuarios concurrentes y ninguna configuración llegó a fallar. Tampoco representan producción.

## Comparación: escalar un monolito frente a escalar un microservicio

Inferido a partir de estos datos: en un monolito habría que replicar la aplicación completa (usuarios, pedidos,
pagos, notificaciones…) para ganar capacidad en el catálogo; aquí se replicó solo product-service
(~95 MiB por réplica) con `docker compose up -d --scale product-service=3`, sin tocar el resto ni
reiniciar el gateway. El análisis completo se desarrollará en la documentación de arquitectura.

## Limitaciones

- Un solo equipo: k6, el gateway, los servicios y PostgreSQL compiten por la misma CPU y memoria.
- Los conteos del gateway son solicitudes públicas; las llamadas entre microservicios (por ejemplo,
  order-service a product-service) no pasan por el gateway y no están incluidas.
- La proporción 80/20 del escenario mixto es un supuesto de modelado, no un dato de uso real.
- Los conteos del gateway incluyen las pocas solicitudes de preparación de cada ejecución.
