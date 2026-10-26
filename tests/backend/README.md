# Backend con PostgreSQL aislado

Este arnés reutiliza las fixtures, migraciones y entornos virtuales de los seis
microservicios. Ejecuta copias verificadas del código contra una instancia
PostgreSQL desechable. No usa el Compose principal ni el PostgreSQL activo.

Ejecución registrada: run ID `backend-20261025-054720-4e530f17` (25-10-2026),
575 casos, 575 aprobados, 0 fallidos, 0 omitidos. Los resultados se generan en
`tests/backend/results/<run-id>/`, que no se versiona.

Entorno soportado: Windows 10/11, Python 3.12 y Docker Desktop (motor Linux con
WSL 2). El arnés usa rutas de Windows (`.venv\Scripts\python.exe`, `%TEMP%`)
y no está preparado para Linux ni macOS.

## Recursos y destino fijos

- Una instancia de la imagen local ya revisada:
  `sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`
  (`postgres:16-alpine`). `--pull never` impide descargas o cambios de versión.
- Red bridge exclusiva `uce-backend-net-<run-id>`, sin conectarse a las redes de
  aplicación, datos u observabilidad. El puerto solo se publica en
  `127.0.0.1:55433`; no se usa la red del host.
- Datos en `tmpfs`, máximo 256 MiB; no se crea ni monta un volumen PostgreSQL.
  El directorio de inicialización es una copia temporal montada de solo lectura.
- Límite de 512 MiB de memoria, sin swap adicional, una CPU, `shared_buffers=32MB`,
  `max_connections=50` y `work_mem=2MB`. El tmpfs cuenta dentro del consumo del
  contenedor. Los servicios se ejecutan en serie.
- Contraseñas aleatorias de pruebas, distintas por rol, creadas durante
  `prepare`. Solo se guardan en el espacio temporal del usuario, nunca en los
  resultados ni en el repositorio. No se utiliza autenticación host `trust`.

La configuración utiliza la sintaxis y el comportamiento descritos en la
[documentación oficial de tmpfs de Docker](https://docs.docker.com/engine/storage/tmpfs/).

| Servicio | Base dentro de la instancia temporal | Rol | Esquema | Casos previstos |
|---|---|---|---|---:|
| inventory-service | inventory_db | inventory_svc | public | 51 |
| payment-service | payments_db | payment_svc | public | 41 |
| order-service | orders_db | order_svc | order_tests | 281 |
| user-service | users_db | user_svc | public | 87 |
| product-service | products_db | product_svc | public | 33 |
| notification-service | notifications_db | notification_svc | public | 82 |
| **Total** | | | | **575** |

Los nombres de bases y roles se conservan porque forman parte de las expectativas
de las pruebas existentes. **No son las bases activas**: la instancia, puerto,
contraseñas y marca de identidad son independientes.

## Barreras de aislamiento

1. El espacio de trabajo está bajo `%TEMP%\uce-backend-isolated\<run-id>` y se
   rechazan enlaces y junctions. Solo se copian `app`, `tests`, `alembic` y
   `alembic.ini`, con extensiones permitidas. No se copian `.env`, secretos,
   claves ni `.venv`.
2. El entorno del proceso se construye con una lista explícita de variables del
   sistema y valores de pruebas. No hereda contraseñas, rutas JWT, variables
   libpq o configuración de negocio. Las fixtures generan sus propias claves.
3. Cada copia tiene un `sitecustomize.py` que instala la guarda antes de importar
   la aplicación. Un error de instalación termina Python con código 86; no se
   permite que Python ignore el fallo y continúe.
4. La guarda valida los parámetros efectivos de conexión: host literal
   `127.0.0.1`, puerto `55433`, base, rol y huella de la contraseña de esa ejecución.
   Rechaza rutas alternativas como `hostaddr`, servicios libpq o archivos de
   contraseñas. Después de conectar, la primera consulta comprueba
   `cluster_name`, `current_database()` y `current_user`, antes de devolver la
   conexión a migraciones o pruebas. Una discrepancia cierra la conexión.
5. Un fallo de aislamiento no deriva de `Exception`, para que las fixtures no lo
   conviertan en `pytest.skip`. Además queda auditado; aunque un test capturase
   el fallo, el controlador rechaza la ejecución.
6. Los subprocesos usan el mismo intérprete y la misma guarda. Se rechazan shell,
   pérdida del `PYTHONPATH` controlado y opciones `-S`, `-I`, `-E` o combinaciones
   equivalentes. Los arranques se relacionan mediante tokens, sin asumir que el
   PID del launcher de Windows sea el PID final de Python.
7. Se bloquea abrir `.env` o archivos de la carpeta de secretos del proyecto.
   El tráfico Python saliente se limita a loopback y se rechazan los puertos
   conocidos de la plataforma. Se permiten los puertos efímeros de los servidores
   HTTP de prueba y los canales internos de asyncio en Windows.
8. Antes de las suites se comprueban IDs, etiquetas, imagen, puerto, límites,
   montajes y pertenencia exclusiva a la red temporal. Los archivos originales,
   copias, guardas y políticas se verifican con SHA-256. No hay flags para omitir
   estas comprobaciones ni para aceptar skips.

El arnés protege frente a errores accidentales de configuración en las pruebas
existentes. No es un sandbox de seguridad para ejecutar código hostil con acceso
al equipo o permisos Docker.

## Preparación en un clon limpio

Requisitos: Windows, Python 3.12 (`py -3.12`), Docker Desktop en ejecución y el
puerto `127.0.0.1:55433` libre. No hace falta levantar el Compose del proyecto
ni crear `.env` o `secrets/`: el arnés no los lee.

### 1. Entornos virtuales de los seis servicios

El arnés ejecuta cada suite con el intérprete del propio servicio. Desde la raíz
del repositorio:

```powershell
$Services = 'inventory-service', 'payment-service', 'order-service',
    'user-service', 'product-service', 'notification-service'
foreach ($Service in $Services) {
    py -3.12 -m venv "backend\$Service\.venv"
    & "backend\$Service\.venv\Scripts\python.exe" -m pip install -r "backend\$Service\requirements-dev.txt"
    if ($LASTEXITCODE -ne 0) { throw "Falló la instalación de $Service" }
}
```

Si un `.venv` ya existe, `py -3.12 -m venv` lo conserva y `pip install` solo
completa lo que falte.

### 2. Imagen de PostgreSQL

`prepare` usa `--pull never` y solo acepta la imagen cuyo ID local sea exactamente
`sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`.
Esa referencia es el digest de la imagen oficial `postgres:16-alpine` publicada en
Docker Hub (índice multiplataforma, incluye `linux/amd64`). Se descarga por
digest, no por etiqueta, para obtener siempre la misma versión:

```powershell
docker pull postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea
docker image inspect sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea --format "{{.Id}}"
```

El segundo comando debe imprimir exactamente el mismo `sha256:721873c3…`, que es
la comparación que hace `prepare`. Esto se cumple con Docker Desktop usando el
almacén de imágenes de containerd (`docker info --format "{{.DriverStatus}}"`
muestra `io.containerd.snapshotter.v1`; en Docker Desktop: *Settings → General →
Use containerd for pulling and storing images*). Con el almacén clásico el ID
local sería el digest de la configuración y `prepare` se detendría con «The
approved local PostgreSQL image is unavailable».

Validado en Windows con Python 3.12 y Docker Desktop con containerd.

### 3. Pruebas unitarias del arnés

Desde la raíz del repositorio:

```powershell
& '.\backend\order-service\.venv\Scripts\python.exe' -B `
    -m unittest discover -s tests/backend -p test_harness.py -v

& '.\backend\order-service\.venv\Scripts\python.exe' -B `
    tests/backend/run_isolated_backend.py --help
```

Los tests utilizan respuestas Docker y conexiones SQL falsas. Las pruebas de
arranque de Python sustituyen el controlador SQL por una función que no permite
conectar. No importan fixtures de negocio, no ejecutan suites backend y no crean
recursos Docker. Los temporales de Windows requieren un entorno con permiso de
escritura en la carpeta temporal del usuario.

### 4. Ejecución de las seis suites

Comprobar primero RAM disponible, carga del equipo, disponibilidad de Docker y
ausencia de k6. `prepare` comprueba imagen local, puerto libre, estados existentes
y ausencia de k6 antes de crear recursos. Usar siempre un run ID nuevo; el arnés
rechaza colisiones y no sobrescribe resultados.

```powershell
# Run from the repository root.
$Repo = (Get-Location).Path
$Python = Join-Path $Repo 'backend\order-service\.venv\Scripts\python.exe'
$Harness = Join-Path $Repo 'tests\backend\run_isolated_backend.py'
$RunId = 'backend-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' +
    [Guid]::NewGuid().ToString('N').Substring(0, 8)

try {
    & $Python -B $Harness prepare --run-id $RunId
    if ($LASTEXITCODE -ne 0) { throw 'Falló la preparación aislada' }

    & $Python -B $Harness run --run-id $RunId `
        --services inventory-service payment-service order-service
    if ($LASTEXITCODE -ne 0) { throw 'Falló el bloque prioritario' }

    & $Python -B $Harness run --run-id $RunId `
        --services user-service product-service notification-service
    if ($LASTEXITCODE -ne 0) { throw 'Falló el bloque restante' }
}
finally {
    & $Python -B $Harness cleanup --run-id $RunId
    if ($LASTEXITCODE -ne 0) { Write-Error 'Revisar la limpieza del run ID registrado' }
}
```

`prepare` también se detiene si algún contenedor existente está `unhealthy` o
`starting`. El resultado de cada suite queda en
`tests/backend/results/<run-id>/summary.json` y en un JUnit XML por servicio.

No hace falta arrancar otros microservicios de pruebas. El inicializador SQL
existente crea bases y roles solo en la instancia nueva. Las migraciones de cinco
servicios se aplican antes de su suite; `order-service` conserva su fixture que
crea y migra `order_tests`. No se ejecutan jobs `*-migrate` del Compose activo ni
se crea su administrador inicial.

El primer bloque contiene 373 casos, con prioridad en inventario, pagos y Saga;
el segundo contiene 202. Se ejecutan suites completas, sin modificar umbrales,
timeouts de las pruebas, fixtures o lógica de negocio. Los tests de persistencia
pueden iniciar y detener sus propios procesos locales, nunca los contenedores
activos. Cualquier fallo, skip, recuento distinto o auditoría incompleta impide
aceptar la suite. No se repite automáticamente un servicio ya intentado.

## Evidencias y limpieza

Los resultados se conservan en `tests/backend/results/<run-id>/`, excluido de Git:

- JUnit XML por servicio y logs de migración/pruebas.
- Logs de preparación con versiones de Python y paquetes, y log Docker redactado.
- Resumen con recuentos, códigos de salida y fechas UTC reales.
- Huellas de fuentes y guardas, auditoría de procesos/conexiones y manifiesto de
  recursos sin contraseñas.
- Informe final de limpieza, comparación de IDs/estado/salud de los contenedores
  originales e indicador de aceptación de las seis suites.

Cada acción tiene un bloqueo exclusivo. Si una acción termina abruptamente, el
bloqueo solo se recupera tras comprobar que su PID y fecha de creación ya no
corresponden a un proceso vivo. Un timeout de una suite detiene su proceso raíz
y solo sus descendientes registrados con PID y fecha de creación coincidentes.

La limpieza exige IDs completos de contenedor y red, nombre exacto, etiqueta y
nonce de la ejecución. Si se pierde una respuesta de creación, solo se recupera
por el nombre único esperado y se vuelve a comprobar la propiedad. La red solo
se elimina si está vacía. El espacio temporal solo se borra después de resolver
su ruta y verificar el manifiesto. Los XML y logs se conservan; las contraseñas
de pruebas y claves temporales desaparecen con el espacio de trabajo.

No se usan `compose down`, `prune`, eliminación de volúmenes, búsquedas generales
de contenedores ni terminaciones por nombre de proceso. Si Docker no responde o
no puede verificarse una identidad, el arnés se detiene y conserva los registros:
no sustituye la limpieza exacta por una eliminación amplia. Tras recuperar acceso,
se puede repetir únicamente `cleanup --run-id <run-id>`.

Los datos en tmpfs no sobreviven a detener/eliminar la instancia temporal. Antes
de una ejecución real deben verificarse el consumo disponible, el comportamiento
de los montajes Docker Desktop, los permisos y la capacidad de limpieza.
