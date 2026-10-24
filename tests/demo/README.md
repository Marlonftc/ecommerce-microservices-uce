# Guion de demostración en vivo

Comandos para mostrar, frente al tribunal, que el sistema funciona. PowerShell, desde la carpeta del proyecto.
Los ejemplos de la tienda usan el puerto 8090; cámbialo si usas otro. La consola recibe la URL explícitamente
y no lee `.env`.
Ninguna prueba borra datos. **Nunca uses `docker compose down -v`.**

Grafana es la **interfaz gráfica principal**: abre [el dashboard local](http://127.0.0.1:3000/d/uce-stage-a).
La Etapa A muestra disponibilidad, historial y tiempos de las sondas HTTP de los seis microservicios,
el Gateway y el frontend. Su configuración y ejecución independiente están en
[observability/README.md](observability/README.md). Todavía no incorpora métricas k6 ni de contenedores.

Durante la defensa, usa una terminal para los comandos y el navegador para Grafana y la tienda.
Los pasos de escalado, parada o carga de este guion se ejecutan manualmente cuando corresponda;
no forman parte de las comprobaciones de lectura de los monitores.

El monitor de consola es una **herramienta opcional**, fuera de Docker. Solo hace consultas de lectura y muestra
estados HTTP y Docker independientes, Gateway y frontend separados, latencias p50/p95 de las sondas,
antigüedad de las mediciones, últimas transiciones y reparto sintético por `X-Instance` cuando se habilita.

```powershell
backend\order-service\.venv\Scripts\python.exe -B tests\demo\monitor.py --gateway http://127.0.0.1:8090
# Take one bounded snapshot and exit:
backend\order-service\.venv\Scripts\python.exe -B tests\demo\monitor.py --gateway http://127.0.0.1:8090 --once
```

Para una vista de CPU y memoria puedes usar `docker stats` en otra terminal.

## Uso de la consola y precisión de las mediciones

El entorno Python 3.12 existente de order-service es suficiente. La consola, el recolector y el lector histórico
usan la biblioteca estándar; no requieren paquetes nuevos ni una sesión gráfica. Docker Desktop debe estar
accesible para consultar contenedores; si no lo está, Docker aparece como UNKNOWN y las sondas HTTP continúan.

### Parámetros de ejecución

Ejecuta los comandos desde la raíz del proyecto. El monitor no busca configuración en archivos.

| Parámetro | Valor por defecto | Comportamiento |
|---|---|---|
| `--gateway URL` | `http://127.0.0.1:8080`; usa `GATEWAY_PORT` si está definida en la terminal | URL HTTP/HTTPS local, sin credenciales, ruta adicional ni parámetros |
| `--project NOMBRE` | `ecommerce-uce` | Filtra los contenedores por la etiqueta de proyecto Docker Compose |
| `--interval SEGUNDOS` | `3` | Espera entre tandas HTTP completadas; mínimo 1 s |
| `--timeout SEGUNDOS` | `2` | Timeout de socket HTTP; entre 0,2 y 10 s |
| `--traffic-rate REQ_S` | `0` | Peticiones adicionales al catálogo; admite valores entre 0 y 5, incluidos decimales |
| `--once` | Desactivado | Espera `timeout + 1,5 s`, imprime una instantánea y termina; alguna lectura puede seguir incompleta |
| `-h`, `--help` | — | Muestra la ayuda y termina |

```powershell
# Reduce polling frequency; keep extra synthetic traffic disabled:
backend\order-service\.venv\Scripts\python.exe -B tests\demo\monitor.py --gateway http://127.0.0.1:8090 --interval 5 --timeout 2
# Enable up to one extra catalogue request per second:
backend\order-service\.venv\Scripts\python.exe -B tests\demo\monitor.py --gateway http://127.0.0.1:8090 --traffic-rate 1
```

La consola se actualiza cada segundo. Cada tanda contiene ocho GET; no se encolan tandas mientras una está
pendiente. Docker se consulta cada 5 s, con un timeout de `timeout + 1 s`. Las consultas se ejecutan en hilos
separados. Ctrl+C solicita la parada; una consulta en curso puede tardar hasta su timeout en terminar.

El catálogo extra **arranca desactivado**. `--traffic-rate` es un límite aproximado: espera la respuesta anterior
antes de repetir. Sus barras incluyen únicamente esas peticiones adicionales, no las ocho sondas básicas,
el tráfico de la tienda ni el de k6. Las muestras caducan a los 10 s; una réplica que Docker confirma detenida
queda excluida del reparto actual. Estas métricas sintéticas **no equivalen al tráfico total del sistema**.

Para evitar la interferencia de la consola durante una medición k6, ciérrala con Ctrl+C y vuelve a abrirla
al terminar. `--traffic-rate 0` elimina solo las peticiones extra: las sondas básicas siguen activas.
Grafana/Blackbox mantiene su propio sondeo independiente; cerrar la consola no lo detiene.

### Interpretación

Esta tabla describe los estados de la consola; Grafana utiliza sus propias sondas de la Etapa A.

| Estado | HTTP | Docker |
|---|---|---|
| UP | Respuesta esperada para la ruta | Todos los contenedores del componente están running y healthy |
| DOWN | Sin respuesta, HTTP 502 o 504 | Lectura válida sin contenedores en ejecución |
| DEGRADED | HTTP 500/503 u otra respuesta inesperada, o contenido incorrecto de Gateway/frontend | Hay contenedores activos, pero alguno está detenido, starting, unhealthy o sin health conocido |
| UNKNOWN | Sin medición, dato antiguo o falta de visibilidad por Gateway caído | Error de Docker o lectura desactualizada |

User, order, payment y notification responden 401 sin sesión: confirma acceso a la ruta, **no valida su base
ni una operación autenticada**. Inventory puede responder 404 si no hay stock para el producto 1.
La consola consulta Gateway en `/health` y comprueba su JSON; el frontend se comprueba en `/` como HTML.
No consulta directamente las rutas internas `/health/ready`; Grafana/Blackbox sí supervisa los endpoints
internos configurados en la Etapa A. Docker conserva sus healthchecks existentes.

Cada fila de consola muestra estado HTTP y Docker por separado, y la edad de la última sonda.
Las lecturas HTTP caducan tras `max(8 s, 2 × intervalo + timeout)` y las de Docker tras
`max(12 s, 2 × intervalo Docker + timeout)`. Un error de Docker invalida la lectura inmediatamente.
Las lecturas HTTP sin vigencia aparecen como UNKNOWN y sus latencias dejan de mostrarse como valores actuales.

p50/p95 se calculan por interpolación lineal, solo con respuestas esperadas de la sonda en los últimos 60 s.
No son las latencias de k6 ni un indicador del rendimiento total. Con pocas muestras, los percentiles tienen
poca representatividad; una sola muestra produce p50 = p95. Sin muestras aparece `—`, nunca un cero inventado.

El recolector conserva hasta 200 transiciones de esta sesión y la consola muestra las cinco últimas.
La duración es el intervalo aproximado entre detectar un problema y confirmar su recuperación;
puede incluir períodos sin visibilidad.
UNKNOWN no confirma una caída ni cierra un incidente. Las caídas más cortas que el sondeo pueden no observarse.
El monitor no reinicia, detiene, escala ni repara contenedores y no verifica el resultado de la Saga.

### Lector reutilizable de históricos k6

`monitor_history.py` se conserva como módulo reutilizable, cubierto por `test_monitor.py`.
**La consola no muestra estos históricos y Grafana todavía no consume directamente sus JSON.**
No hay una integración k6 en tiempo real implementada en esta etapa.

`load_runs()` lee `tests/load/results/` sin modificarla y asocia `<run_id>.json` con `<run_id>-env.json`,
comprobando identificador y escenario. Devuelve ejecuciones y avisos de archivos incompletos o inválidos;
un resumen sin contexto conserva réplicas y código de salida desconocidos. No procesa `.log` ni expone
`extra_env` o variables de los contenedores. `HistoryReader` ofrece una lectura periódica opcional cada 10 s
para futuros consumidores; la consola no lo inicia.

El lector conserva la fecha con su zona horaria, milisegundos, req/s, porcentajes de error/checks y los estados
de umbral registrados. Código k6 99 indica umbrales incumplidos; sin contexto no se supone éxito.
Son **resultados de pruebas anteriores**, no mediciones actuales ni puntos de una serie temporal reconstruida.

Ofrece validación de compatibilidad para ejecuciones `scale` de 1 y 3 réplicas: carga, entorno y salidas 0.
Si faltan datos o difieren, la comparación es solo descriptiva. No promedia percentiles. Sostenida usa
`count / sustained_s`; rampa usa `count / (ramp_up_s + hold_s)`, según `scale_config` registrado.
La tasa global incluye preparación y calentamiento; no mide por sí sola la capacidad máxima.

El dashboard web de k6 en tiempo real es una alternativa independiente:
ver [DASHBOARD_K6.md](DASHBOARD_K6.md). Está documentada, sin modificar Compose ni el runner.

### Pruebas y capturas

Usa Grafana para las capturas del informe. La consola solo conserva sus sondas durante la sesión;
cerrarla no altera los JSON de k6 ni el historial almacenado por Prometheus.

```powershell
# Run isolated fixtures and read existing reports without stopping containers:
backend\order-service\.venv\Scripts\python.exe -B -m unittest discover -s tests/demo -p test_monitor.py -v
```

Las fixtures se usan únicamente en pruebas aisladas, nunca como mediciones reales.
Las pruebas de lectura histórica requieren los informes existentes del repositorio.

---

## 0. Antes de empezar

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d
do { Start-Sleep 5; $s = docker compose ps --format "{{.Service}} {{.Status}}" } while ($s -match "starting")
$s
```

Todo debe aparecer `healthy`. Abre la tienda en `http://127.0.0.1:8090`.

## 1. Escalar solo el catálogo (punto 8 del caso)

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d --no-deps --scale product-service=3 product-service
docker compose ps product-service
```

**En `docker compose ps`:** comprueba los 3 contenedores de product-service. La consola opcional, iniciada con
`--traffic-rate 1`, permite observar el reparto por `X-Instance`. Tras la actualización DNS de Nginx y varias
respuestas aparecen las identidades y barras; su tamaño depende de las muestras sintéticas de los últimos 10 s.
Grafana en la Etapa A muestra disponibilidad HTTP, no un inventario de todas las réplicas.

**Qué decir:** «El API Gateway también es el balanceador de carga. Encuentra las réplicas por el DNS de Docker sin
reiniciarse y les reparte las peticiones por turnos. En un monolito habría que copiar la aplicación entera.»

Comprobación sin monitor:

```powershell
1..30 | ForEach-Object { (Invoke-WebRequest "http://127.0.0.1:8090/api/products?limit=1" -UseBasicParsing).Headers["X-Instance"] } | Group-Object | Select-Object Name, Count
```

## 2. Prueba de carga (punto 9)

```powershell
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py browse
```

**En Grafana:** las sondas pueden reflejar la latencia bajo carga, pero no sustituyen las métricas k6 ni
garantizan cero errores. Cierra la consola opcional con Ctrl+C para evitar sus consultas durante la medición;
Grafana/Blackbox sigue realizando sus propias sondas. Consulta los resultados k6 al terminar.
**En `docker stats`:** las réplicas de product-service consumen CPU a la vez; los demás servicios, poco.
**Al terminar,** k6 muestra peticiones por segundo, tiempos (p95) y porcentaje de errores. El detalle queda en
`tests\load\results\`.

Otras cargas:
- `run_load.py purchase`: compras completas.
- `run_load.py mixed`: 80 % navegación y 20 % compras.
- `run_load.py scale --replicas 1 --repeat 1` y luego `--replicas 3`: compara 1 y 3 réplicas.

Los resultados ya medidos están en `docs/testing/09-carga.md`.

## 3. Se cae una réplica (punto 7)

```powershell
docker compose ps product-service --format "{{.Name}}"
docker stop ecommerce-uce-product-service-2
```

**En la consola opcional:** product-service puede seguir **UP por HTTP** mientras Docker indica **DEGRADED** por la réplica
detenida. Al actualizarse Docker, esa identidad sale del reparto actual; las muestras también caducan a los 10 s.
Puede haber errores durante la actualización de Nginx. Las barras requieren `--traffic-rate` mayor que 0.

**Qué decir:** «Las otras réplicas atienden; el cliente no lo nota.»

## 4. Se cae un servicio completo (punto 7)

```powershell
docker compose stop notification-service
```

1. **En Grafana:** notification-service pasa a **DOWN** cuando las sondas detectan la caída. La consola opcional
   puede mostrar HTTP 502 a través del Gateway; los demás componentes mantienen sus estados según sus sondas.
2. **En la tienda:** compra un producto. El pago se aprueba igual. La campana del cliente muestra **«?»**: no puede
   consultar, pero no inventa un 0.
3. **Como administrador:** en **Administración → Operaciones** aparece «Enviar notificación» pendiente, con reintentos.
4. Vuelve a levantarlo:

```powershell
docker compose start notification-service
```

Cuando el monitor muestra **UP**, a los pocos segundos el worker entrega la notificación y la campana del cliente
pasa a 1 (al cambiar de página o en menos de un minuto).

**Qué decir:** «La compra no depende de que el aviso salga en ese momento: el paso queda guardado y se reintenta.»

Lo mismo con otros servicios:
- `inventory-service` detenido: el checkout queda «Reservando tus productos» y termina al volver.
- `product-service`: el catálogo muestra un error con «Reintentar».
- `user-service`: el administrador ve los pedidos con «Cliente #ID».

## 5. Persistencia (punto 5)

```powershell
docker compose down
docker compose -f compose.yaml -f compose.override.example.yaml up -d
```

Vuelve a entrar con la misma cuenta: los usuarios, productos, pedidos y notificaciones siguen ahí (volumen
`postgres-data`).

**Qué decir:** «Lo que vive en PostgreSQL sobrevive a los contenedores. Lo temporal, como `/tmp` de Nginx o la sesión
del navegador, puede perderse. Solo `down -v` borraría los datos.»

## 6. Seguridad del gateway

```powershell
curl.exe -s http://127.0.0.1:8090/internal/products/1
curl.exe -s -D - -o NUL http://127.0.0.1:8090/
```

- La primera responde 404 JSON: las rutas internas no son accesibles desde fuera.
- La segunda muestra las cabeceras `Content-Security-Policy`, `X-Frame-Options` y `X-Request-ID`.
- En la página de login, doce intentos seguidos con contraseña incorrecta muestran «Demasiados intentos de inicio de
  sesión». Es el límite de 10 por minuto.

## 7. Suites automáticas (si piden «ejecuta las pruebas»)

```powershell
backend\order-service\.venv\Scripts\python.exe -m pytest tests\gateway -v      # 34 pruebas, ~4 min
cd frontend; npm run test; cd ..                                             # 368 pruebas, ~30 s
```

Las pruebas E2E necesitan el sistema con tiempos cortos:

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml -f compose.e2e.yaml up -d --build
backend\order-service\.venv\Scripts\python.exe -m pytest tests\e2e -v         # 12 pruebas, ~5 min
cd tests\ui; npx playwright test --headed; cd ..\..                           # 22 pruebas con el navegador visible
docker compose -f compose.yaml -f compose.override.example.yaml up -d          # volver a lo normal
```

`--headed` abre el navegador y se ve a Playwright usar la tienda.

## Al terminar

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d --no-deps --scale product-service=1 product-service
docker compose start notification-service
docker compose ps
```
