# Validación de Etapa B · preparación sin carga

Este registro corresponde a la preparación anterior a la activación del receptor. La primera prueba
autorizada posterior está documentada en [SMOKE_K6_20261024.md](SMOKE_K6_20261024.md), con recepción real
confirmada. Se conserva esta instantánea para distinguir las fases de validación.

Fecha: 24 de octubre de 2026. Se implementó la configuración, el runner opcional, el dashboard y el smoke.
**No se ejecutó el binario k6, no se inició ninguna prueba de carga y no se crearon, recrearon, escalaron
ni detuvieron contenedores.** No se instalaron dependencias ni se realizaron operaciones Git de escritura.

## Resultados comprobados

| Validación | Resultado real |
|---|---|
| Runner con Docker/HTTP simulados | 16 pruebas aprobadas |
| Contratos de observabilidad | 16 aprobadas: 9 de Etapa A y 7 del dashboard/smoke |
| Monitor de consola e históricos conservados | 27 pruebas existentes aprobadas |
| Total unitario | 59 aprobadas |
| Compose estático | 4 configuraciones válidas: observabilidad, carga original, carga + Remote Write y smoke independiente |
| JSON de dashboard | Válido y coincidente con su generador independiente |
| PromQL en Prometheus 3.15.1 | 23 consultas aceptadas, incluidas comparaciones habilitadas/deshabilitadas |
| Semántica PromQL | 6 fixtures calculadas de solo lectura: RPS 10, errores ponderados 10 %, cero justificado, ausencia de tráfico, actividad caducada y primer error aislado |
| Sintaxis JavaScript smoke | Aceptada por `node --input-type=module --check`, sin ejecutar el script |
| Integridad | 396 archivos protegidos/conservados con SHA-256 idéntico antes/después |
| Evidencia de Etapa A | `evidence/historial-real.png` intacta y sin seguimiento |

Las fixtures del runner usan archivos temporales con datos identificados como fixtures y comandos simulados.
El sandbox de Windows bloqueó inicialmente su escritura temporal; al repetir las mismas pruebas fuera de
esa restricción pasaron. No se utilizó la carpeta real de resultados como destino de esas pruebas.
Los seis casos PromQL son vectores calculados durante consultas: no se enviaron por Remote Write,
no se persistieron y no están presentes como métricas o resultados del dashboard.

El JSON nuevo quedó aprovisionado automáticamente por el polling de Grafana, sin reiniciarlo:
UID `uce-k6`, título **UCE — Pruebas de carga k6**, 16 paneles. Se revisó visualmente su presentación
sin muestras: ausencia de curvas y tarjetas Sin datos, sin confundirla con estado UP ni rellenar con cero.
Las comparaciones permanecen vacías hasta usar un enlace generado por el validador local de JSON.

## Estado del despliegue existente

Lectura de la Etapa A a las 20:23:35 UTC del 24/10/2026:

- Diez comprobaciones de `check_observability.py validate` en true.
- Ocho componentes UP y quince sondas frescas/exitosas.
- Grafana 13.2.4 con base interna OK; HTML de tienda y catálogo público correctos.
- Prometheus sigue con `web.enable-remote-write-receiver=false` en el proceso existente.
- Prometheus, Grafana y Blackbox conservaban sus arranques anteriores y RestartCount 0.

La configuración de archivo ya contiene el flag nuevo, pero **no está aplicado al proceso Prometheus**.
No confundir la disponibilidad del dashboard con recepción k6 probada. El dashboard A, reglas, targets,
fuente de datos y Blackbox no se modificaron. Memoria, CPU, retención y volúmenes de Prometheus se conservaron.

## Pendiente de autorización

1. Aplicar únicamente el cambio del servicio Prometheus según [K6.md](K6.md), preservando su volumen.
2. Confirmar receiver=true y que la Etapa A sigue sana.
3. Autorizar y ejecutar por separado el smoke: un VU, ocho GET al catálogo y ninguna escritura de negocio.
4. Comprobar entrega, etiquetas, ocho solicitudes, histogramas reales, unidades, stale markers y consumo.
5. Solo después, autorizar benchmarks y la comparación 1/3 con la misma carga/entorno.

La aceptación sintáctica de consultas no verifica ingestión ni precisión de muestras reales. Las unidades
se configuraron según el formato nativo documentado; la comprobación empírica requiere el smoke pendiente.
No hay mediciones nuevas de rendimiento, ninguna curva k6 real ni evidencia para ampliar límites.

## Archivos preparados

Modificados:

- `tests/load/run_load.py`, `tests/load/README.md`.
- `tests/demo/observability/compose.observability.yaml`, `test_observability.py`, `README.md`.
- `tests/demo/README.md`, `tests/demo/DASHBOARD_K6.md`.

Nuevos:

- `tests/load/compose.prometheus.yaml`, `k6_observability.py`, `smoke_prometheus.js`, `test_run_load.py`.
- `tests/demo/observability/compose.k6-smoke.yaml`, `generate_k6_dashboard.py`, `test_k6_dashboard.py`, `validate_k6.py`.
- `tests/demo/observability/grafana/dashboards/uce-k6.json`, `K6.md`, `VALIDACION_K6.md`.

Los resultados históricos, escenarios k6 existentes, consola, seis microservicios, frontend, Gateway y
Compose principal se conservaron. No incluye cAdvisor ni otras etapas.
