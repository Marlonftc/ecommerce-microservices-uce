# Alternativa: dashboard web integrado de k6

Estado: propuesta documentada, **no ejecutada ni incorporada al despliegue**. No se cambió `compose.yaml`,
`compose.load.yaml` ni `tests/load/run_load.py`. Grafana es la interfaz gráfica principal de la plataforma;
la Etapa A supervisa sondas HTTP y todavía no integra métricas k6. Esta alternativa permite observar una
prueba k6 en tiempo real cuando el autor decida ejecutarla. La [Etapa B](observability/K6.md) implementa
por separado Remote Write hacia Grafana; el receptor está activo y el primer smoke fue verificado.
El dashboard nativo descrito en este documento sigue siendo una alternativa documentada sin ejecutar.

El monitor de consola es opcional y `monitor_history.py` se conserva como lector reutilizable de resultados
anteriores. La consola no muestra esos históricos y Grafana todavía no consume directamente sus JSON.

## Encaje con la estructura actual

`compose.load.yaml` ya define `grafana/k6:1.0.0`, la red `ecommerce-net` y los montajes `/scripts` y `/results`.
k6 consulta `http://gateway`, sin atravesar los puertos de depuración del host. Su dashboard está integrado
desde k6 0.49; no requiere Grafana ni otro microservicio.

Hay dos enlaces diferentes: dentro del contenedor el dashboard debe escuchar en `0.0.0.0`; la publicación
Docker se restringe a `127.0.0.1:5665:5665`. Así se puede abrir desde este equipo, sin publicar el puerto
en todas las interfaces del host. `docker compose run` requiere `-p` explícito para ese enlace.

## Ejecución manual futura, sin editar archivos

Ejemplo PowerShell desde la raíz, **con el sistema ya levantado**. Este comando ejecuta una prueba de carga;
no forma parte de las comprobaciones de lectura del monitor. El escenario `scale.js` hace GET sobre productos
existentes y conserva la cantidad de réplicas actual: este comando no escala ni arranca dependencias.

```powershell
$demoRunId = 'scale-dashboard-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
docker compose -f compose.yaml -f compose.load.yaml run --rm --no-deps `
  -p 127.0.0.1:5665:5665 `
  -e K6_WEB_DASHBOARD=true `
  -e K6_WEB_DASHBOARD_HOST=0.0.0.0 `
  -e K6_WEB_DASHBOARD_PORT=5665 `
  -e K6_WEB_DASHBOARD_OPEN=false `
  -e "K6_WEB_DASHBOARD_EXPORT=/results/$demoRunId.html" `
  -e "RUN_ID=$demoRunId" `
  k6 run /scripts/scale.js
```

En otra ventana del navegador abre **http://127.0.0.1:5665** durante la prueba. El informe HTML se exporta
al montaje de resultados al terminar. Cierra la pestaña del dashboard para permitir que k6 finalice;
el proceso puede esperar mientras haya clientes conectados. El contenedor temporal y su puerto desaparecen
al salir. Si 5665 está ocupado, cambia el puerto izquierdo del mapeo y la URL del navegador.

## Diferencias con el runner existente

- Esta ejecución escribe el resumen JSON mediante el `handleSummary()` existente y un HTML nuevo; no sobrescribe
  informes anteriores si se conserva un `RUN_ID` único. No genera el archivo `-env.json` del runner.
- El lector `monitor_history.py` puede analizar ese resumen histórico, pero conserva réplicas y código de
  salida desconocidos. No será elegible para la comparación verificada 1 vs. 3 sin contexto real.
  No se fabrica ese contexto ni se importa automáticamente el resumen en Grafana.
- `run_load.py` sí recoge réplicas, CPU/memoria, reparto y salida de k6, pero actualmente no acepta `-p` para
  el `docker compose run` que construye. Pasarle solo `-e K6_WEB_DASHBOARD=true` no publica el puerto.
  Además, captura la salida y limita el proceso a 900 s; mantener un navegador conectado puede retrasar su fin.
- Integrar la publicación con el runner, o añadir un override específico, requiere una autorización independiente.
  No basta con agregar `ports` a un servicio cuando se usa `compose run` sin `--service-ports` o `-p`.
- Para evitar las consultas de la consola durante una nueva medición, ciérrala con Ctrl+C y ábrela al terminar.
  `--traffic-rate 0` desactiva solo el tráfico extra; las sondas básicas continúan. Las métricas sintéticas de
  la consola no representan el tráfico total del sistema. Grafana/Blackbox mantiene su sondeo independiente.
  Los escenarios browse/purchase/mixed preparan datos de prueba; no son comprobaciones de solo lectura.

Docker Compose puede cargar la configuración local al ejecutar este ejemplo. El monitor no ejecuta ese comando
ni lee `.env`. No se deben copiar credenciales al comando, al informe o a las capturas.

## Fuentes oficiales consultadas

- [Dashboard web y opciones de entorno de k6](https://grafana.com/docs/k6/latest/results-output/web-dashboard/).
- [Introducción del dashboard en k6 0.49](https://github.com/grafana/k6/blob/master/release%20notes/v0.49.0.md).
- [Docker Compose run: publicación manual de puertos y --no-deps](https://docs.docker.com/reference/cli/docker/compose/run/).

Los parámetros se contrastaron con documentación; la apertura del puerto y la ejecución del dashboard
quedan pendientes de un ensayo autorizado. No se descargaron imágenes ni se ejecutó k6 en esta implementación.
