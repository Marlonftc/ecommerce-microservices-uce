# Pruebas de carga (k6)

k6 se ejecuta en un contenedor (`grafana/k6:1.0.0`, definido en `compose.load.yaml`)
conectado a `ecommerce-net` y envía todas las solicitudes al gateway (`http://gateway`).
No se instala nada en Windows ni se modifica ningún microservicio.

La integración opcional `--prometheus` envía series reales a Grafana mediante Remote Write e histogramas
nativos. El receptor está activo y el primer smoke de 1 VU/8 GET terminó correctamente; ver
[resultados de integración](../demo/observability/SMOKE_K6_20261024.md). No se han autorizado benchmarks
ni comparaciones 1/3. No se ejecutan cargas
al pedir ayuda, importar el runner ni ejecutar sus pruebas aisladas. Ver
[Etapa B: despliegue, etiquetas, comparación y smoke](../demo/observability/K6.md).

Sin esa opción se conserva el flujo anterior. `RUN_ID`/`RESULTS_DIR` se reservan y los resúmenes se validan
desde un directorio aislado antes de publicarlos, evitando colisiones incluso si falla setup.
Los JSON, logs, umbrales y códigos k6 se mantienen. Los resultados antiguos no se convierten en curvas.

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py browse
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py purchase
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py mixed
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py scale --replicas 1 --repeat 3
backend\order-service\.venv\Scripts\python.exe tests\load\run_load.py scale --replicas 3 --repeat 3
docker compose -f compose.yaml -f compose.override.example.yaml up -d --no-deps --scale product-service=1 product-service
```

| Script | Escenario |
|---|---|
| `browse.js` | Navegación del catálogo: listado, detalle y existencias, de 0 a 20 usuarios en 2 minutos |
| `purchase.js` | 4 compradores, cada uno con su cliente: carrito, checkout, pago APPROVE, pedido PAID y CONFIRMED |
| `mixed.js` | ~80 % de las solicitudes públicas de navegación y ~20 % de compra, a tasa fija |
| `scale.js` | product-service con 1 o 3 réplicas: calentamiento, tasa sostenida y rampa en modelo cerrado |
| `lib.js`, `purchase_flow.js` | Funciones comunes |
| `run_load.py` | Escala product-service, comprueba las réplicas, mide CPU y memoria y cuenta la demanda por servicio en el log del gateway |

- Datos: browse/purchase/mixed crean sus propios productos `LOAD-<id>` con stock amplio y sus propios clientes;
  al terminar desactiva esos productos. No modifica datos existentes. Nunca se usa `docker compose down -v`.
  scale solo consulta productos existentes, aunque el runner sí escala sus réplicas. El smoke separado no escala.
- Credenciales: la cuenta de administrador llega a k6 desde `.env` por `compose.load.yaml`; las
  contraseñas de los clientes son aleatorias y solo existen en memoria. Los resúmenes no guardan tokens.
- Login: se respeta el límite del gateway (10/min); si responde 429, k6 espera `Retry-After` y repite.
- Resultados en `results/`: `<run_id>.json` (resumen de k6), `<run_id>.log` y `<run_id>-env.json`
  (réplicas, CPU y memoria, demanda por servicio en el gateway).
- Un código de salida 99 de k6 significa que algún umbral no se cumplió; los resultados se conservan.
