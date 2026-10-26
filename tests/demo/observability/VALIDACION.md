# Validación real · Etapa A

Fecha: **24 de octubre de 2026**, hora de Ecuador (UTC−05).
Implementación y verificaciones limitadas a `tests/demo/observability/`.

## Resultados

| Comprobación | Resultado observado |
|---|---|
| Red externa descubierta por etiquetas Docker | `ecommerce-uce_ecommerce-net` |
| Puerto antes del arranque | `127.0.0.1:3000` disponible; comprobación mediante bind exclusivo |
| Docker Compose `config --quiet` | Correcto, con `compose.vars` explícito |
| Promtool `check config` | Configuración válida; 16 reglas encontradas |
| Pruebas Python finales | **9/9**, `unittest`, 0,011 s en la ejecución final |
| Pruebas promtool de reglas | **12 casos, SUCCESS**, fixtures en contenedor temporal sin red |
| Arranque `up -d --wait` | Grafana, Prometheus y Blackbox `healthy` |
| Grafana | Versión 13.2.4; dashboard aprovisionado y abierto en navegador |
| Prometheus | 15 targets Blackbox recogidos y 15 muestras vigentes satisfactorias |
| Componentes | Gateway, frontend y los seis microservicios en **UP** |
| Tienda | `/` responde 200 HTML; `/api/products?limit=1` responde 200 con catálogo |
| Red de datos | Ningún contenedor de observabilidad pertenece a `data-net` |
| Puertos efectivos | Solo Grafana: `127.0.0.1:3000 -> 3000/tcp`; 9090 y 9115 sin publicación |
| Integridad | Los 11 archivos protegidos comparados mediante SHA-256 permanecen idénticos |

La última validación completa registrada se realizó a las **14:05:38 de Ecuador**
(`2026-10-24T19:05:38.446398+00:00`). Todas las comprobaciones del script devolvieron `true`, incluida
la verificación de que Grafana había cargado el mapeo final de la cronología.

Se revisaron en el navegador las tarjetas, las series de los seis microservicios y la cronología.
La captura [historial-real.png](evidence/historial-real.png) muestra las ocho bandas UP, sin extenderlas
al período anterior al inicio de la recolección. Los valores proceden del stack real, sin fixtures.

Los casos aislados verifican disponibilidad, degradación, caída, ausencia de muestras, falta de readiness,
fallo del exporter, datos antiguos, caída/recuperación confirmadas, huecos UNKNOWN y atribución de fallos
entre Gateway y frontend. Las fixtures de `rules.test.yml` nunca se cargan en el Prometheus real.
**No se provocaron caídas ni se detuvieron contenedores de comercio.** Por ello, las transiciones de fallo
están verificadas por pruebas aisladas; no se presentan como incidentes reales observados en la plataforma.

## Recursos medidos

Una muestra con el dashboard abierto, antes de la última recarga de aprovisionamiento de Grafana:

| Contenedor | CPU según `docker stats` | Memoria usada / límite |
|---|---|---|
| Grafana | 6,32 % | 668,5 MiB / 768 MiB |
| Prometheus | 3,71 % | 60,38 MiB / 384 MiB |
| Blackbox | 0,68 % | 17,96 MiB / 128 MiB |
| Total de esa muestra | 10,71 % de un núcleo, sumando las lecturas | **746,84 MiB** |

Es una muestra puntual, no una media ni un pico certificado. En una muestra anterior se observaron
aproximadamente 555 MiB totales. Docker expresa CPU respecto a un núcleo: no representa un 10,71 %
de todas las CPU del equipo. El máximo configurado conjunto es 1,25 GiB y 1,25 CPU.
En la inspección de esa muestra los tres contenedores tenían `restarts=0` y `oom=false`.

Los tamaños lógicos de las imágenes informados por Docker fueron:

- Grafana: 1.938.171.456 bytes.
- Prometheus: 381.391.975 bytes.
- Blackbox: 58.269.468 bytes.

Suman aproximadamente **2,38 GB lógicos**, antes de considerar capas compartidas y compresión.
Los volúmenes son adicionales. La retención Prometheus es 24 h / 256 MB para bloques, con WAL y datos activos aparte.

## Ajustes realizados durante la validación

- Se amplió el límite de Grafana de 512 a 768 MiB después de observar unos 481 MiB utilizados inicialmente.
- Se corrigió la cronología para mapear estados discretos; el modo de umbrales numéricos mostraba rangos.
- Se configuró polling de aprovisionamiento cada 30 s: los eventos de archivos de un bind mount de Windows
  no estaban propagando los cambios con el intervalo inicial de 10 s.
- Solo Grafana se recreó para aplicar esos ajustes. Prometheus recargó las reglas finales mediante SIGHUP,
  sin detenerse. No se recrearon ni modificaron componentes de comercio.

## Límites y alcance

El historial empieza con este stack; no se importan pruebas antiguas ni se inventan intervalos previos.
Esta etapa observa salud por HTTP y no certifica operaciones de negocio, Saga ni salud individual de cada réplica.
Los monitores Python anteriores se conservaron sin cambios. No se cargó el `.env` de la aplicación,
no se consultaron claves privadas ni contraseñas y no se hicieron operaciones Git.
cAdvisor, métricas Docker e integración k6 quedan fuera de esta etapa y requieren nueva autorización.
