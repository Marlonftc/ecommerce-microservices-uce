# E-Commerce Microservices UCE

Plataforma de comercio electrónico basada en microservicios y Docker.

Universidad Central del Ecuador - Carrera de Sistemas de Información
Autor: Marlon Fernando Tituaña Castillo (GitHub: [Marlonftc](https://github.com/Marlonftc))

## Descripción

La plataforma separa las funciones de una tienda en línea en seis microservicios independientes, cada uno con su propia base de datos, que se comunican mediante APIs REST dentro de una red Docker.

| Servicio | Responsabilidad |
|---|---|
| user-service | Registro, inicio de sesión y roles |
| product-service | Catálogo de productos |
| inventory-service | Existencias y reservas |
| order-service | Carrito y pedidos (con un worker de reconciliación) |
| payment-service | Pagos simulados |
| notification-service | Notificaciones |

Además se incluyen un API Gateway (Nginx), un frontend (React) y PostgreSQL.

```text
Navegador ──> 127.0.0.1:<GATEWAY_PORT> ──> gateway (Nginx)
                                             ├── /        -> frontend (React, Nginx sin privilegios)
                                             └── /api/... -> los seis microservicios
red ecommerce-net: gateway, frontend y microservicios
red data-net (interna): microservicios y PostgreSQL
```

El gateway es el único puerto publicado y solo escucha en `127.0.0.1`. El navegador usa un único origen:
la aplicación y la API comparten dirección, sin CORS.

## Tecnologías

Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, Nginx 1.28, React 18, Vite, TypeScript, Docker y
Docker Compose. Pruebas: pytest, Vitest, Playwright y k6.

## Estructura

```text
backend/        microservicios (uno por carpeta, con sus pruebas)
frontend/       aplicación web y su imagen Docker
gateway/        configuración de Nginx
infra/          inicialización de PostgreSQL y generación de claves JWT
tests/          pruebas del gateway, E2E (API e interfaz) y de carga
docs/           decisiones de arquitectura y resultados de pruebas
compose.yaml                     sistema completo
compose.override.example.yaml    puertos de depuración en 127.0.0.1 (opcional)
compose.e2e.yaml                 tiempos cortos y frontend de demostración, solo para pruebas
compose.load.yaml                k6, solo para pruebas de carga
.env.example
```

## Requisitos

- Docker Desktop con WSL 2 (Docker Compose v2) y Git.
- Python 3.12, solo para generar las claves JWT y ejecutar las pruebas de pytest.
- Node.js 22 (22.12 o posterior), solo para desarrollar el frontend y ejecutar Vitest o Playwright.

## Puesta en marcha en otro equipo

Los comandos son para PowerShell y se ejecutan desde la carpeta del proyecto.

### 1. Clonar y configurar

```powershell
git clone https://github.com/Marlonftc/ecommerce-microservices-uce.git
cd ecommerce-microservices-uce
Copy-Item .env.example .env
```

Editar `.env` y reemplazar **todos** los valores `change_me...` por contraseñas propias. Variables
principales:

| Variable | Uso |
|---|---|
| `GATEWAY_PORT` | Puerto del gateway en `127.0.0.1` (por defecto 8080; cambiarlo si está ocupado) |
| `POSTGRES_PASSWORD`, `*_DB_PASSWORD` | Superusuario (solo para crear las bases) y un usuario por servicio |
| `INTERNAL_API_KEY` | Clave de las llamadas entre servicios (`/internal`) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | Administrador inicial, creado por `user-migrate` |
| `PAYMENT_SIMULATION_ENABLED` | `true` permite los modos simulados de pago (rechazo y aprobación lenta) |
| `VITE_PAYMENT_DEMO` | `true` muestra esos modos en la interfaz; se aplica al **construir** la imagen del frontend |

El archivo `.env` y la carpeta `secrets/` están en `.gitignore`: nunca se suben al repositorio.

### 2. Generar las claves JWT

user-service firma los tokens con una clave privada RSA; los demás servicios solo reciben la pública.

```powershell
py -3.12 -m venv backend\order-service\.venv
backend\order-service\.venv\Scripts\python.exe -m pip install -r backend\order-service\requirements-dev.txt
backend\order-service\.venv\Scripts\python.exe infra\jwt\generate_keys.py
```

Las claves quedan en `secrets/jwt_private.pem` y `secrets/jwt_public.pem` y se montan como secretos de
Docker Compose. El mismo entorno virtual sirve después para las pruebas de pytest.

### 3. Levantar el sistema

```powershell
docker compose up -d --build
docker compose ps -a
```

Resultado esperado: los servicios, el worker, el frontend y el gateway en `running (healthy)`; los
trabajos `*-migrate` en `Exited (0)`. La primera construcción tarda varios minutos.

Abrir `http://127.0.0.1:8080` (o el `GATEWAY_PORT` elegido) y entrar con `ADMIN_EMAIL` y `ADMIN_PASSWORD`.
Los clientes se registran desde la propia aplicación. La API está en `http://127.0.0.1:8080/api/...`.

Ante un problema: `docker compose logs <servicio>`.

### 4. Modo demostración de pagos

Por defecto la interfaz no muestra los modos simulados. Para una demostración:

```powershell
# En .env: VITE_PAYMENT_DEMO=true y PAYMENT_SIMULATION_ENABLED=true
docker compose build frontend
docker compose up -d frontend payment-service
docker image inspect ecommerce-uce/frontend --format '{{json .Config.Labels}}'
```

La salida del último comando debe incluir `"uce.payment-demo":"true"`. (Se pide la lista completa de etiquetas
en JSON porque Windows PowerShell elimina las comillas dobles internas de un formato como
`index .Config.Labels "..."`.)

`VITE_PAYMENT_DEMO` queda dentro del JavaScript al construir la imagen: cambiar la variable sin
reconstruir no tiene efecto. Una variable con el mismo nombre en la terminal tiene prioridad sobre `.env`
(`Remove-Item Env:VITE_PAYMENT_DEMO` la elimina). Para volver al modo normal, poner `false` y repetir
los dos primeros comandos.

## Pruebas

| Pruebas | Cómo | Detalle |
|---|---|---|
| Frontend (Vitest) | `cd frontend; npm ci; npm run typecheck; npm run test; npm run build` | `frontend/README.md` |
| Cada microservicio (pytest) | Desde la carpeta del servicio, con PostgreSQL publicado por `compose.override.example.yaml` | `docs/testing/01` a `07` |
| Gateway (pytest) | `docker compose -f compose.yaml -f compose.override.example.yaml up -d --build` y `backend\order-service\.venv\Scripts\python.exe -m pytest tests\gateway -v` | `tests/gateway/README.md` |
| E2E de la API (pytest) | Sistema con `compose.e2e.yaml` y `backend\order-service\.venv\Scripts\python.exe -m pytest tests\e2e -v` | `tests/e2e/README.md` |
| E2E de la interfaz (Playwright) | Sistema con `compose.e2e.yaml` y `cd tests\ui; npm ci; npx playwright install chromium; npx playwright test` | `tests/ui/README.md` |
| Carga (k6) | `tests\load\run_load.py` | `tests/load/README.md` |

Las pruebas crean sus propios datos (prefijos `GWT-`, `E2E-`, `UI6-`, `LOAD-`), leen la cuenta de
administrador de `.env` sin mostrarla y desactivan o dejan sin efecto lo que crean. Ninguna usa
`docker compose down -v`.

### Volver a la configuración normal después de las pruebas E2E

`compose.e2e.yaml` acorta los tiempos de order-service y usa la imagen `ecommerce-uce/frontend:e2e`
(con las opciones de demostración). No basta con dejar de usar ese archivo: hay que recrear los
contenedores y comprobarlo.

```powershell
docker compose -f compose.yaml -f compose.override.example.yaml up -d
docker compose ps frontend --format "{{.Image}}"                 # ecommerce-uce/frontend
docker image inspect ecommerce-uce/frontend --format '{{json .Config.Labels}}'   # incluye "uce.payment-demo":"false"
$html = (Invoke-WebRequest http://127.0.0.1:8080/ -UseBasicParsing).Content
$js = [regex]::Match($html, '/assets/index-[\w-]+\.js').Value
(Invoke-WebRequest "http://127.0.0.1:8080$js" -UseBasicParsing).Content.Contains('Opciones de demostración')   # False
```

## Detener el sistema

```powershell
docker compose stop       # detiene los contenedores; se reanudan con docker compose start
docker compose down       # elimina contenedores y redes; los datos de PostgreSQL se conservan
```

> **Atención:** `docker compose down -v` elimina también el volumen `postgres-data`, es decir, **todos los
> datos** (usuarios, productos, pedidos). Solo debe usarse para empezar desde cero de forma deliberada.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `port is already allocated` | El puerto del gateway está ocupado: cambiar `GATEWAY_PORT` en `.env` y repetir `docker compose up -d` |
| `secrets/jwt_private.pem` no encontrado | Faltan las claves: paso 2 |
| `set in .env` al iniciar | Falta una variable obligatoria en `.env` |
| Un servicio queda `unhealthy` | `docker compose logs <servicio>`; suele ser una contraseña de base distinta a la usada al crear el volumen |
| La interfaz muestra (u oculta) las opciones de pago sin esperarlo | Revisar la etiqueta `uce.payment-demo` y si hay una variable `VITE_PAYMENT_DEMO` en la terminal |
| `/` responde `UPSTREAM_UNAVAILABLE` | El frontend está detenido o arrancando: `docker compose up -d frontend`; la API sigue funcionando |

## Limitaciones

- Mientras un pedido está en proceso de compra, el carrito no se puede modificar. Si el pago se aprueba, el carrito se vacía; si se rechaza o se cancela el pedido, se conserva.
- Se usa una sola instancia de PostgreSQL con una base de datos y un usuario por servicio, por lo que el aislamiento es lógico y no físico.
- Los pagos y las notificaciones son simulados.
- Los tokens de acceso duran 30 minutos. Desactivar un usuario o cambiar su rol no invalida los tokens que ya tenía: los servicios que solo verifican la firma seguirán aceptándolos hasta que expiren.
- El sistema se sirve por HTTP en `127.0.0.1`. Un despliegue real necesitaría TLS (y entonces HSTS).

Las decisiones de diseño están en [docs/architecture/decisiones.md](docs/architecture/decisiones.md).
