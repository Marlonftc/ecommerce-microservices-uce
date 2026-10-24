# Pruebas E2E de la interfaz (Playwright)

Prueban la aplicación React contra el sistema completo en Docker, siempre a través del
gateway (`http://127.0.0.1:<GATEWAY_PORT>`, el único puerto publicado). Usan Chromium y un solo
worker, en el orden de los archivos: comparten el inventario del sistema y algunas detienen
microservicios. Se ejecutan desde el equipo anfitrión porque necesitan `docker compose`.

```powershell
# 1. Sistema con tiempos cortos y el frontend de demostración (imagen ecommerce-uce/frontend:e2e)
docker compose -f compose.yaml -f compose.override.example.yaml -f compose.e2e.yaml up -d --build

# 2. Pruebas (la primera vez: npm ci y npx playwright install chromium)
cd tests\ui
npm ci
npx playwright install chromium
npx playwright test
cd ..\..

# 3. Volver al frontend normal y a los tiempos normales (ver README principal)
docker compose -f compose.yaml -f compose.override.example.yaml up -d
```

| Archivo | Qué prueba |
|---|---|
| `01-spa-security.spec.ts` | Recarga directa de rutas, página «no encontrada», `/api` y `/internal` en 404 JSON, cabeceras, caché y CSP |
| `02-auth.spec.ts` | Registro, sesión al recargar, cierre de sesión, contraseña incorrecta, regreso tras login, permisos de cliente |
| `03-shopping.spec.ts` | Catálogo, búsqueda, detalle, carrito, checkout, pago aprobado, rechazado, aprobación lenta y cancelación |
| `04-notifications.spec.ts` | Notificación de un pedido pagado, enlace al pedido, marcar como leída, filtro |
| `05-admin.spec.ts` | Productos e inventario (incluida una respuesta perdida), desactivar cuentas, compradores, notificaciones de clientes |
| `06-failures.spec.ts` | product-, notification-, inventory- y user-service detenidos; operación FAILED reintentada por el administrador |

## Datos y credenciales

- La cuenta de administrador se lee del `.env` raíz (`ADMIN_EMAIL`, `ADMIN_PASSWORD`); nunca se muestra
  ni se guarda.
- Cada ejecución tiene un identificador propio. Los clientes se crean como `ui6-<ejecución>-…@example.com`
  con contraseñas aleatorias que solo existen en memoria; los productos usan el SKU `UI6-<ejecución>-…`
  y su propio stock. No se usan ni modifican productos o cuentas existentes.
- Las pruebas que necesitan sesión obtienen el token por la API y lo escriben en `sessionStorage` con el
  mismo formato que guarda `AuthContext` (`storageState` de Playwright no conserva `sessionStorage`).
  Solo `02-auth` inicia sesión por el formulario.
- El gateway admite 10 inicios de sesión por minuto por dirección: las pruebas cuentan sus intentos y
  esperan cuando hace falta; ante un 429 esperan `Retry-After` y repiten.
- Al terminar (aunque haya fallos) `global-teardown.ts` vuelve a arrancar cualquier servicio que haya quedado
  detenido y desactiva los productos y cuentas de la ejecución (los servicios no tienen borrado físico).
  Nunca se usa `docker compose down -v`.

## Resultados y reportes

- `playwright-report/` (HTML) y `test-results/` (capturas solo de pruebas fallidas) están en `.gitignore`.
- Las trazas están desactivadas porque registran las cabeceras de las peticiones (tokens de prueba).
  `UI_TRACE=1` las activa para depurar en el propio equipo; no se deben compartir.
- `.run/` guarda el estado de la ejecución (ids de los datos de prueba y servicios detenidos, sin tokens ni
  contraseñas) y se borra al terminar la limpieza.

## Variables opcionales

| Variable | Uso |
|---|---|
| `UI_TRACE=1` | Guarda trazas de las pruebas fallidas |
| `UI_CHROMIUM_PATH` | Ruta a un Chromium ya instalado, en lugar del que descarga Playwright |
| `UI_SERVICE_CONTROL` | Comando que reemplaza a `docker compose stop/start` (`<comando> stop|start|wait <servicio>`); solo para ejecutar la suite contra un entorno sin Docker |
