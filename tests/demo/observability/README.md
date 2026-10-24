# Observabilidad local UCE · Etapas A y B

Grafana + Prometheus + Blackbox Exporter, en un proyecto Compose independiente llamado
`uce-observability`. Observa seis microservicios, Gateway y frontend mediante HTTP.
El dashboard de la Etapa A y sus sondas se conservan. La Etapa B añade el receptor interno de k6 y un
dashboard independiente: [configuración y procedimiento](K6.md). La
[primera prueba smoke](SMOKE_K6_20261024.md) confirmó recepción real sin modificar la plataforma. No incluye cAdvisor.
El monitor de consola se conserva sin cambios. No ejecutar cargas ni recrear contenedores sin autorización.

## Ejecución en PowerShell

Requisitos: Docker Desktop con los contenedores de la tienda ya iniciados y el Python existente del proyecto.
No requiere instalar paquetes Python. La red de aplicación se comprobó con las etiquetas reales de Docker:
`ecommerce-uce_ecommerce-net`.

Desde la raíz del repositorio:

```powershell
Set-Location 'C:\Users\Msi GF63\Desktop\UCE\ecommerce-microservices-uce'
$obs = @(
  '--project-name', 'uce-observability',
  '--env-file', 'tests/demo/observability/compose.vars',
  '--project-directory', 'tests/demo/observability',
  '-f', 'tests/demo/observability/compose.observability.yaml'
)

# Antes del primer arranque: comprueba la red correcta y que el puerto esté libre.
backend\order-service\.venv\Scripts\python.exe -B tests/demo/observability/check_observability.py preflight
docker compose @obs config --quiet
docker compose @obs pull
docker compose @obs up -d --wait --wait-timeout 90

# Verificación de lectura: Grafana, Prometheus, sondas y entrada pública de la tienda.
backend\order-service\.venv\Scripts\python.exe -B tests/demo/observability/check_observability.py validate --wait 60
docker compose @obs ps
```

Abrir: **http://127.0.0.1:3000/d/uce-stage-a**.
El dashboard se aprovisiona automáticamente; no hay que importarlo ni iniciar sesión.
Se permite únicamente acceso anónimo con rol Viewer, restringido al puerto de localhost.
No se crea un administrador inicial ni se utiliza una contraseña del proyecto.

`compose.vars` está intencionalmente vacío. Pasarlo con `--env-file` evita cargar el `.env` de la tienda.
Mantén también el nombre explícito de proyecto: no combines este Compose con el principal.

Si el stack ya está iniciado, el preflight informa que 3000 está ocupado: usa `validate` y `ps`.
Si pertenece a otra aplicación, resuelve el conflicto antes de arrancar; los scripts no terminan procesos
ni detienen servicios para liberar el puerto.

Para detener o reanudar **solo observabilidad**, cuando el autor lo decida:

```powershell
docker compose @obs stop
docker compose @obs start
```

Los volúmenes propios conservan el historial y la configuración. La red externa no es propiedad de este stack.
Si se recrea o elimina la red de la tienda, coordina el ciclo de vida de ambos proyectos.

## Qué se supervisa

| Componente | Rutas consultadas desde Blackbox |
|---|---|
| user-service | `http://user-service:8000/health/live`, `/health/ready` |
| product-service | `http://product-service:8000/health/live`, `/health/ready` |
| inventory-service | `http://inventory-service:8000/health/live`, `/health/ready` |
| order-service | `http://order-service:8000/health/live`, `/health/ready` |
| payment-service | `http://payment-service:8000/health/live`, `/health/ready` |
| notification-service | `http://notification-service:8000/health/live`, `/health/ready` |
| Gateway | `http://gateway/health` |
| Frontend directo | `http://frontend:8080/healthz` |
| Tienda por Gateway | `http://gateway/` (HTTP 200, tipo HTML y doctype) |

Son **15 sondas cada 10 segundos**, aproximadamente 1,5 GET/s. Cada sonda tiene timeout de 3 s y
Prometheus un timeout de recogida de 4 s. Las reglas se evalúan cada 5 s. No se siguen redirecciones.
Los cuerpos de salud se verifican: no basta con recibir HTTP 200 con HTML o un JSON incorrecto.

Los endpoints `/health/ready` son reales y verifican la conexión del propio servicio con su base.
El stack de observabilidad no se conecta a PostgreSQL: solo lee HTTP en `ecommerce-net`.
No modifica la exposición pública del Gateway ni publica las rutas internas de salud.

Las sondas usan el nombre DNS del servicio. Si hay varias réplicas de product-service, cada consulta
comprueba una dirección resuelta; esta etapa **no certifica individualmente todas las réplicas**.

## Estados y ausencia de datos

| Valor | Estado | Evidencia |
|---|---|---|
| 3 | UP | En APIs: live y ready válidos; en Gateway/frontend: su healthcheck válido |
| 2 | DEGRADED | API con live válido y ready fallido, por ejemplo HTTP 503 |
| 1 | DOWN | Sonda live/edge completada con fallo de conexión, timeout, HTTP inesperado o cuerpo inválido |
| 0 | UNKNOWN | Falta una medición necesaria, el exporter no pudo recogerse o los datos tienen más de 25 s |

`up` de Prometheus informa sobre la recogida del exporter; `probe_success` informa sobre el objetivo.
Las reglas distinguen ambos y comprueban antigüedad. Una recogida fallida no se convierte en caída del servicio.
El inventario fijo de ocho componentes solo aporta el valor **UNKNOWN** cuando falta información: nunca inventa UP.

Gateway y frontend son independientes. El estado principal del frontend corresponde a `/healthz`.
La tarjeta **Tienda a través del Gateway** verifica además su HTML: es DEGRADED si falla con el Gateway sano,
y UNKNOWN si el fallo impide atribuirlo al frontend por caída del Gateway.
Ninguna sonda confirma una compra completa, el resultado de la Saga o la entrega de notificaciones.

## Lectura del dashboard

- Ocho tarjetas de estado al final del rango temporal seleccionado.
- Cronología con bandas UP/DOWN/DEGRADED/UNKNOWN, fechas y duración aproximada al pasar el puntero.
- Duraciones de sondas en milisegundos, con huecos si no hay muestras fiables. No se rellenan con cero ni se unen huecos.
- Recuentos de entradas confirmadas en DOWN y recuperaciones confirmadas a UP durante el rango.
- Códigos HTTP vigentes y antigüedad de la última muestra visible por ruta.
- Comprobación independiente del HTML de la tienda a través del Gateway.

La duración incluye DNS, conexión y respuesta. Una sonda fallida completada puede durar el timeout;
no es una latencia de negocio, un percentil k6 ni una medida del tráfico total.
Los conteos requieren estados consecutivos conocidos: UNKNOWN no crea un incidente ni una recuperación.
Una recuperación desde DEGRADED también cuenta como retorno a UP. Una primera observación DOWN no
inventa un cambio previo desde UP. Los fallos más cortos que el intervalo de sondeo pueden no observarse.

La tarjeta de antigüedad máxima usa solo series aún visibles: si falta una serie, la tarjeta UNKNOWN del componente
sigue siendo la evidencia de falta de cobertura. Las tablas pueden omitir rutas sin datos; no significan que estén sanas.
Si Prometheus no responde, Grafana muestra error de consulta o ausencia de datos; una imagen anterior no confirma salud actual.

El rango inicial es de 30 minutos, actualización cada 10 s y zona horaria `America/Guayaquil`.
El historial comienza con el primer arranque: los espacios anteriores son ausencia de datos, no disponibilidad histórica.
Para capturas del informe, abre el enlace en una ventana amplia del navegador; en un panel estrecho Grafana apila las tarjetas.

## Aislamiento, recursos y almacenamiento

Solo Grafana publica un puerto: **127.0.0.1:3000**. Prometheus y Blackbox no publican puertos al host.
Grafana y Prometheus pertenecen únicamente a `uce-observability_monitoring`.
Blackbox pertenece también a la red externa comprobada `ecommerce-uce_ecommerce-net`.
Ninguno se incorpora a `data-net`, monta el socket Docker o recibe credenciales del proyecto.

| Contenedor | Límite RAM | Límite CPU | PIDs |
|---|---|---|---|
| Grafana | 768 MiB | 0,50 CPU | 256 |
| Prometheus | 384 MiB | 0,50 CPU | 128 |
| Blackbox | 128 MiB | 0,25 CPU | 64 |
| Total máximo configurado | 1,25 GiB | 1,25 CPU | — |

Estos son límites, no reservas ni consumos medidos. Grafana tuvo inicialmente 512 MiB y se amplió su margen
tras observar aproximadamente 481 MiB durante la revisión del dashboard.
Prometheus conserva hasta 24 h y aplica un límite de retención de 256 MB a bloques TSDB.
El WAL, los bloques activos y la configuración ocupan espacio adicional: no es un límite absoluto del volumen.
Se limita la concurrencia de consultas a cuatro. El filtro del job Blackbox conserva sus métricas utilizadas;
no filtra las series recibidas por Remote Write. En la Etapa B, k6 limita sus etiquetas en el origen.
Las imágenes están fijadas: Grafana 13.2.4, Prometheus 3.15.1, Blackbox 0.29.1.

## Pruebas sin provocar caídas

```powershell
backend\order-service\.venv\Scripts\python.exe -B -m unittest discover -s tests/demo/observability -p test_observability.py -v

# Fixtures aisladas: contenedor temporal sin red, no modifica el Prometheus en ejecución.
$obsDir = (Resolve-Path tests/demo/observability).Path
docker run --rm --network none --memory 256m --cpus 0.5 `
  --mount "type=bind,source=$obsDir,target=/work,readonly" --workdir /work `
  --entrypoint /bin/promtool prom/prometheus:v3.15.1 test rules rules.test.yml

backend\order-service\.venv\Scripts\python.exe -B tests/demo/observability/check_observability.py validate --wait 60
```

`rules.test.yml` contiene fixtures para UP, DOWN, DEGRADED, UNKNOWN, datos antiguos, caída del exporter y transiciones.
**No se carga en el Prometheus real**: `rule_files` apunta únicamente a `rules.yml`.
`validate` devuelve código 1 si no puede confirmar todos los objetivos UP; no intenta repararlos ni cambia contenedores.
Su comprobación pública usa el Gateway observado en 8090: si cambia ese puerto, adapta la constante de validación antes de usarla.

`generate_dashboard.py` regenera de forma determinista `targets.json`, `rules.yml`, `rules.test.yml` y el dashboard JSON.
No se necesita ejecutarlo para arrancar. Grafana revisa los archivos aprovisionados cada 30 s mediante polling,
para detectar cambios también en los bind mounts de Docker Desktop para Windows.
Si se modifica la definición, hay que repetir las pruebas y recargar únicamente
las reglas/configuración de observabilidad. El dashboard es de solo lectura en la interfaz y se actualiza desde el archivo aprovisionado.

Resultados reales de esta implementación: [VALIDACION.md](VALIDACION.md).

## Archivos

`compose.observability.yaml` y `compose.vars`: despliegue independiente y carga de variables vacía.
`prometheus.yml`, `blackbox.yml`, `targets.json`, `rules.yml`: recogida y clasificación.
`grafana/provisioning/` y `grafana/dashboards/`: fuente de datos y dashboard.
`generate_dashboard.py`, `rules.test.yml`, `test_observability.py`, `check_observability.py`: generación y verificación.

## Referencias oficiales

- [Redes externas entre proyectos Compose](https://docs.docker.com/compose/how-tos/networking/).
- [Configuración Blackbox de la versión fijada](https://github.com/prometheus/blackbox_exporter/blob/v0.29.1/CONFIGURATION.md).
- [Pruebas de reglas con promtool](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/).
- [Aprovisionamiento de Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/).

El receptor está activo y la primera prueba smoke terminó correctamente. Otras cargas y comparaciones
requieren autorización adicional. Ver [K6.md](K6.md), [preparación](VALIDACION_K6.md) y
[resultados smoke](SMOKE_K6_20261024.md). cAdvisor queda fuera de esta etapa.
