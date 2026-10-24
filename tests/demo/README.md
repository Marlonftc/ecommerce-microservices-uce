# Guion de demostración en vivo

Comandos para mostrar, frente al tribunal, que el sistema funciona. PowerShell, desde la carpeta del proyecto.
Los ejemplos usan el puerto 8090 (`GATEWAY_PORT` del `.env`); cámbialo si usas otro.
Ninguna prueba borra datos. **Nunca uses `docker compose down -v`.**

Abre **dos ventanas**:
- **Ventana A:** los comandos de cada prueba.
- **Ventana B:** el monitor en vivo.

```powershell
backend\order-service\.venv\Scripts\python.exe tests\demo\monitor.py
```

El monitor solo lee, no crea datos ni usa contraseñas. Se actualiza cada segundo y muestra:
- cada servicio y el frontend: **UP** o **DOWN**, código HTTP y tiempo;
- **el reparto del catálogo entre las réplicas** de product-service, en barras (cabecera `X-Instance`);
- peticiones por segundo, errores y tiempos de respuesta (p50 y p95);
- los contenedores de cada servicio según Docker (en ejecución y healthy).

Para una vista de CPU y memoria puedes abrir una tercera ventana con `docker stats`.

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

**En el monitor:** product-service pasa a tener 3 contenedores y, en unos segundos (Nginx vuelve a consultar el DNS cada
10 s), aparecen **3 barras** de tamaño parecido.

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

**En el monitor:** el tiempo p95 sube mientras dura la carga, el reparto entre réplicas se mantiene y los errores siguen en 0.
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

**En el monitor:** product-service sigue **UP** y, en pocos segundos, quedan 2 barras. Los errores siguen en 0 o, como
mucho, aparece alguno durante los segundos que Nginx tarda en dejar de usar la réplica.

**Qué decir:** «Las otras réplicas atienden; el cliente no lo nota.»

## 4. Se cae un servicio completo (punto 7)

```powershell
docker compose stop notification-service
```

1. **En el monitor:** notification-service pasa a **DOWN (HTTP 502)** y todo lo demás sigue **UP**.
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
