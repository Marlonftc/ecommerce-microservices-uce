# Pruebas del API Gateway

Se ejecutan contra el sistema real en Docker. Todas las llamadas HTTP van a
`http://127.0.0.1:<GATEWAY_PORT>` (8080 por defecto), el único puerto publicado por `compose.yaml`. Los
comandos de Docker se usan para leer el log del gateway, revisar redes y
escalar, detener o pausar contenedores, por eso se ejecutan desde el equipo
anfitrión.

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d --build
backend\order-service\.venv\Scripts\python.exe -m pytest tests\gateway -v -s
```

- Duración aproximada: 4 a 5 minutos (incluye una espera de 30 s por timeout y otra de 40 s antes
  de la prueba del límite de login).
- Una ruta no se da por buena solo por su código HTTP: se comprueba el contenido propio del
  endpoint y, en el log del gateway (buscando su `X-Request-ID`), que la dirección del upstream
  pertenece a un contenedor del servicio esperado. Para las rutas bloqueadas se comprueba que el
  upstream está vacío, es decir, que la solicitud no llegó a ningún servicio.
- `test_7_headers.py` reemplaza temporalmente notification-service por un servidor que devuelve
  las cabeceras recibidas (alias de red `notification-service`), con order-worker detenido.
  La configuración del gateway no cambia. Al terminar se restauran ambos servicios.
- Desde la etapa 6 el gateway sirve la aplicación React en `/`: las rutas fuera de `/api` se comprueban
  como servidas por el contenedor `frontend` (nunca por un microservicio) y las rutas `/api` desconocidas
  siguen en 404 JSON sin reenviarse. `test_9_frontend.py` comprueba la navegación SPA, la caché de
  `/assets/`, las cabeceras de seguridad y la CSP en HTML, assets, API y errores (404, 405, 413, 429, 502,
  504), que la imagen normal se construyó con `VITE_PAYMENT_DEMO=false`, y que la API sigue funcionando con
  el frontend detenido y tras reiniciar el gateway (al final vuelve a arrancar el frontend).
- Al final se deja product-service con una réplica y notification-service activo, aunque una
  prueba falle a mitad.
- La cuenta de administrador se lee de `.env`. Los clientes se crean con contraseñas aleatorias
  que no se guardan ni se muestran. Los productos de prueba usan el prefijo `GWT-`.
  Nunca se usa `docker compose down -v`.
