# Pruebas de extremo a extremo

Usan los seis servicios reales en Docker, sin simulaciones. Se ejecutan desde el
equipo anfitrión porque varias pruebas detienen, pausan, matan o escalan
contenedores; un contenedor de pruebas necesitaría el socket de Docker, que da
control total sobre el anfitrión.

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml -f compose.e2e.yaml up -d --build
backend\order-service\.venv\Scripts\python.exe -m pytest tests/e2e -v
```

- `compose.e2e.yaml` acorta los tiempos (reserva de 1 minuto, barrido de 1 minuto, worker cada 2 s).
  Después de las pruebas, vuelve a los tiempos normales con
  `docker compose -f compose.yaml -f compose.override.example.yaml up -d`.
- La cuenta de administrador se lee de `.env` (`ADMIN_EMAIL`, `ADMIN_PASSWORD`). Los clientes se
  crean en cada prueba con contraseñas aleatorias que no se guardan ni se muestran.
- Cada prueba crea sus propios productos (`E2E-...`) y no modifica datos existentes.
  Nunca se usa `docker compose down -v`.
