# Frontend

Aplicación web (React 18, Vite, TypeScript y React Router) de la plataforma. Todas las
llamadas usan la ruta relativa `/api`: el navegador solo habla con el gateway Nginx, que las
reparte entre los microservicios.

## Desarrollo

Requiere Node.js 22 y el sistema en Docker con el gateway publicado.

```powershell
cd frontend
npm ci
$env:GATEWAY_URL = "http://127.0.0.1:8090"   # puerto del gateway (GATEWAY_PORT del .env)
npm run dev                                  # http://127.0.0.1:5173
```

En desarrollo, Vite reenvía `/api` al gateway sin quitar el prefijo. `GATEWAY_URL` solo la lee
Vite al arrancar; no es una variable `VITE_*` y no llega al navegador. Si no se define, se usa
`http://127.0.0.1:8080`, el puerto por defecto de `compose.yaml`.

### Modo demostración de pagos

`VITE_PAYMENT_DEMO` es una variable **de compilación** de Vite: su valor queda fijado al compilar o al
arrancar `npm run dev`. Solo el valor exacto `true` activa las opciones.

```powershell
$env:VITE_PAYMENT_DEMO = "true"   # en la misma terminal, antes de npm run dev o npm run build
npm run dev
Remove-Item Env:VITE_PAYMENT_DEMO  # para volver al modo normal (y reiniciar Vite)
```

En Docker el valor se fija al construir la imagen (ver «Imagen Docker»): cambiarlo exige reconstruirla.
Ocultar las opciones **no es una restricción de seguridad**: es solo una decisión de la interfaz;
order-service acepta cualquiera de los tres modos válidos de cualquier usuario autenticado. Un intento
de pago ya iniciado conserva su modo original aunque el modo demostración se desactive.

## Comprobaciones

```powershell
npm run typecheck   # TypeScript estricto
npm run test        # Vitest
npm run build       # compilación de producción en dist/
```

## Imagen Docker

`Dockerfile` multietapa:

1. `node:22.23.3-alpine` instala las dependencias con `npm ci`, ejecuta `npm run typecheck` y compila
   con `npm run build`.
2. `nginxinc/nginx-unprivileged:1.28.0-alpine` sirve solo `dist/` y `nginx/default.conf`, como usuario
   101 y en el puerto 8080. La imagen final no contiene Node, el código fuente, `node_modules`, las
   pruebas ni secretos (`.dockerignore`).

En `compose.yaml` el servicio `frontend` no publica puertos, solo está en `ecommerce-net`, tiene sistema de
archivos de solo lectura (con `/tmp` en tmpfs), `cap_drop: ALL` y `no-new-privileges`. El navegador llega a
él únicamente por el gateway: `http://127.0.0.1:<GATEWAY_PORT>/`.

- **Navegación SPA**: cualquier ruta que no sea un archivo (`/login`, `/carrito`, `/pedidos/<id>`,
  `/admin/usuarios`…) devuelve `index.html` y React Router muestra la página; una ruta inexistente muestra
  la página «no encontrada» de la aplicación. Un archivo de `/assets/` que no existe da 404.
- **Caché**: `/assets/*` (nombres con hash) se guarda un año (`immutable`); `index.html` usa `no-cache`, así
  que tras un nuevo build el navegador carga los archivos nuevos.
- **Cabeceras de seguridad y CSP**: las añade el gateway (decisión D21), no este contenedor.

`VITE_PAYMENT_DEMO` se pasa como argumento de construcción:

```powershell
# Normal (por defecto false): sin opciones de demostración
docker compose build frontend
docker compose up -d frontend

# Comprobar con qué valor se construyó la imagen: la salida incluye "uce.payment-demo":"false"
docker image inspect ecommerce-uce/frontend --format '{{json .Config.Labels}}'
```

Para la demostración y las pruebas E2E, `compose.e2e.yaml` construye la imagen
`ecommerce-uce/frontend:e2e` con `true`, sin sobrescribir la normal. Una variable `VITE_PAYMENT_DEMO` en la
terminal tiene prioridad sobre `.env` también para Docker Compose.

## Sesión y roles

| Ruta | Acceso |
|---|---|
| `/` | Pública |
| `/productos`, `/productos/:id` | Públicas; agregar al carrito pide iniciar sesión |
| `/login`, `/registro` | Solo sin sesión; con sesión se redirige |
| `/cuenta`, `/carrito`, `/checkout` | Cualquier usuario con sesión (cliente o administrador) |
| `/pedidos`, `/pedidos/:id` | Con sesión. Un administrador ve los pedidos de todos, pero solo paga o cancela los suyos |
| `/notificaciones` | Solo clientes; un administrador ve una página 403 |
| `/admin` | Solo administradores; un cliente ve una página 403 |
| `/admin/productos`, `/admin/productos/nuevo`, `/admin/productos/:id` | Solo administradores |
| `/admin/usuarios`, `/admin/operaciones`, `/admin/notificaciones` | Solo administradores |

- El inicio de sesión usa `POST /api/auth/login`; el perfil y el rol se toman siempre de
  `GET /api/users/me`. Mientras el perfil no se confirma, ninguna página protegida se muestra.
- `sessionStorage` guarda solo el token y su vencimiento: la sesión sobrevive a recargar la
  página, pero no a cerrar la pestaña, y no se comparte entre pestañas. Nunca se guardan
  contraseñas ni el perfil. Riesgo: un script inyectado en la página (XSS) podría leer el token;
  la política de seguridad de contenido lo reduce, pero no lo elimina.
- La sesión termina sola al vencer el token (30 minutos, con 30 s de margen). No hay tokens de
  renovación: el backend no los ofrece.
- Un 401 en el login significa credenciales incorrectas o cuenta desactivada. Un 401 en otra
  llamada cierra la sesión (vencida, inválida o cuenta desactivada), solo si responde a una
  solicitud enviada con el token actual.
- Si user-service no responde al recuperar la sesión, el token se conserva y se ofrece reintentar.
- Cerrar sesión borra el token de este navegador, pero **no lo revoca**: un token copiado antes
  sigue siendo válido en los servicios hasta que vence (decisión D11).
- Ocultar un enlace o una página no da ni quita permisos: los microservicios autorizan cada
  solicitud (401 o 403).

## Catálogo y carrito

**Catálogo** (`GET /api/products`, `GET /api/inventory?ids=`):

- Solo productos activos, 12 por página, ordenados como los entrega product-service. La
  búsqueda (`q`, por nombre), la categoría y la página viven en la URL
  (`/productos?q=&categoria=&pagina=`).
- product-service no tiene un endpoint de categorías: se obtienen de los productos activos,
  leyendo como máximo 5 páginas de 100. **Límite conocido:** con más de 500 productos activos
  la lista de categorías puede quedar incompleta; la página lo indica.
- Disponibilidad: «Disponible (n unidades)», «Agotado» (0 unidades), «Sin existencias
  registradas» (el producto no tiene fila en inventory-service) y «Disponibilidad no verificada»
  (inventory-service no respondió). Los dos primeros casos bloqueados no se pueden agregar; con
  inventario caído sí se puede, porque la reserva real ocurre al comprar.
- El backend no tiene imágenes de productos: se muestra la inicial de la categoría, sin fotos
  de ejemplo.

**Carrito** (`/api/cart`, order-service). Solo existe en PostgreSQL; el navegador guarda en
memoria la última versión confirmada y nunca la escribe en `sessionStorage`.

- `PUT /cart/items/{id}` fija la cantidad **absoluta** (1 a 99). Al agregar desde un producto se
  envía «cantidad actual confirmada + unidades elegidas».
- Cada cambio envía `expected_version` (en el cuerpo del PUT y en la query de los DELETE) con la
  versión del último carrito confirmado. Las versiones solo crecen: una respuesta con una versión
  menor nunca reemplaza un carrito más reciente.
- Los cambios se ejecutan de uno en uno con un bloqueo interno; un segundo envío mientras otro
  está en curso no se manda.
- `CART_CHANGED` y `CART_LOCKED`: se vuelve a leer el carrito, se avisa y **no** se repite el
  cambio automáticamente. `CART_FULL`: máximo 50 productos distintos.
- Timeout, error de red o 502/504 del gateway: el cambio pudo haberse aplicado. No se permite
  otro cambio hasta que `GET /cart` responda de nuevo. Un 502/503 enviado por order-service
  (catálogo sin responder) sí es seguro: el carrito no cambió.
- Si product-service no responde, el carrito se muestra con productos y cantidades pero sin
  precios ni total (`prices_available=false`); se pueden eliminar productos, no cambiar cantidades.
- Subtotales y total estimado son los que calcula order-service; el navegador no suma importes.
  El precio final se confirma al crear el pedido (etapa 4).
- Al cerrar sesión o cambiar de usuario se cancelan las solicitudes pendientes y se ignora
  cualquier respuesta tardía de la sesión anterior.

## Pedidos y pagos

**Checkout** (`POST /api/orders` con `Idempotency-Key` y `{cart_version}`):

- Antes de enviar, el intento se guarda en `sessionStorage` (usuario, versión del carrito, clave y,
  cuando se conoce, el id del pedido). Así un timeout, un error de red o una recarga repiten **la
  misma solicitud con la misma clave**: el backend devuelve el mismo pedido y nunca crea otro. No se
  guarda el token, contraseñas ni datos del perfil, y el registro se borra cuando el resultado se
  conoce.
- `202 RESERVING` (el inventario no respondió): el mismo POST se repite a los 2, 4 y 8 s, nunca dos a
  la vez; después, un reintento manual. Si nadie lo repite, order-service lo cancela a los 2 minutos.
- Timeout, error de red, 500 o errores del gateway: resultado incierto; se recupera con la clave
  original. Solo un 503 enviado por order-service («catálogo no disponible») asegura que no se creó
  ningún pedido.
- `CART_CHANGED`, `CART_EMPTY`, `PRODUCT_NOT_AVAILABLE`, `INSUFFICIENT_STOCK` (el pedido queda
  cancelado) y `CART_LOCKED` (enlace al pedido activo **propio**, también para un administrador).

**Pago simulado** (`POST /api/orders/{id}/pay`). No se usan tarjetas ni dinero real; el pago lo
registra payment-service.

- Normalmente hay un único botón «Pagar», que envía `simulation: APPROVE` de forma explícita.
- Con `VITE_PAYMENT_DEMO=true` aparece además el panel plegado «Opciones de demostración», con
  aprobar, rechazar y aprobación lenta, para demostrar el rechazo y el pago resuelto por el worker.

- El intento (clave y simulación) se guarda antes de enviar y se reutiliza hasta conocer el
  resultado; después del primer intento la simulación no se puede cambiar.
- `202 PAYMENT_PENDING`: se consulta `GET /api/orders/{id}` cada 3 s durante 2 minutos (el worker lo
  resuelve); después, botón «Actualizar». Nunca se paga de nuevo con otra clave.
- Resultado incierto: primero se consulta el pedido; si sigue en `PENDING_PAYMENT`, se ofrece
  repetir con la misma clave y simulación.
- Aprobado: `PAID`, carrito vacío. Rechazado: `PAYMENT_REJECTED`, carrito conservado.

**Estados**: los seis de order-service. `EXPIRED` es un motivo de cancelación, no un estado. Un pedido
`PAID` con inventario `RESERVED` se muestra como «falta confirmar las unidades», y una liberación de
reserva pendiente nunca se presenta como hecha. El plazo de pago (15 min) se muestra con una cuenta
regresiva orientativa: decide la hora del servidor.

**Textos según quién mira**: se decide comparando `user_id` del pedido con el usuario, no por el rol
(un administrador también compra). Un pedido cancelado por su dueño dice «Lo cancelaste tú» a él y
«Lo canceló el cliente» a un administrador.

**Comprador** (solo administradores): order-service guarda únicamente `user_id`. El nombre y el correo
se piden a user-service con **una sola** consulta `GET /api/users?ids=` por página (ids únicos, sin el
propio administrador). Si user-service falla o un usuario no existe, se muestra «Cliente #ID» y los
pedidos se ven igual. Un cliente nunca llama a esta ruta.

## Notificaciones

Solo para clientes: notification-service devuelve a un administrador las notificaciones de todos los
clientes, por lo que no tiene campana (su vista de solo lectura llega en la etapa 5C).

- **Campana**: cuenta las no leídas con el campo `total` de
  `GET /api/notifications?unread_only=true&size=1`. Se actualiza al iniciar la sesión, al abrir
  pedidos o notificaciones, cuando un pedido propio llega a un estado final, al marcar una como leída,
  cada 60 s con la pestaña visible y al volver a la pestaña. Nunca hay dos lecturas a la vez; al cerrar
  sesión o cambiar de usuario se cancelan temporizadores y respuestas pendientes. Si la consulta falla
  muestra «?», nunca 0.
- **`/notificaciones`**: 20 por página, filtro «Solo no leídas» (`?no_leidas=1`), «Marcar como leída»
  (`PATCH /api/notifications/{id}/read`) y enlace al pedido. Títulos y mensajes vienen del servicio y
  se muestran como texto, nunca como HTML.

## Administración de productos e inventario

Solo administradores; product-service e inventory-service vuelven a comprobar el rol en cada llamada.

- **Listado**: `GET /api/products?include_inactive=true` (activos e inactivos, búsqueda por nombre,
  20 por página). Las existencias de la página llegan en **una sola** consulta `GET /api/inventory?ids=`.
  Si inventory-service falla, los productos se muestran igual.
- **Formulario**: las mismas reglas que product-service, que valida otra vez. SKU y categoría se
  muestran como se guardarán (`LAP-003`, `ropa-deportiva`). El precio se trata **solo como texto**:
  se acepta coma o punto, se rechazan los formatos ambiguos (`1.234`, `1.234,50`), más de 2 decimales,
  0, negativos y más de 10 dígitos enteros, y se envía como `"899.90"`, nunca como número decimal.
- **Crear** (`POST`, sin Idempotency-Key en el backend): un resultado incierto nunca se repite solo.
  Se buscan por nombre los productos con ese SKU: si existe con los mismos datos, «sí se creó»; con
  otros datos, no se considera propio; si se revisaron **todos** los resultados y no está, «no se
  creó»; si la búsqueda falla o queda incompleta, el resultado sigue incierto. El SKU único impide
  duplicados en un reintento controlado.
- **Editar** (`PUT`, reemplazo completo): antes de guardar se relee el producto; si `updated_at`
  cambió, se avisa y se pide cargar los datos actuales. Es una comprobación preventiva: entre esa
  lectura y el `PUT` sigue existiendo un margen, porque product-service no controla versiones.
- **Desactivar** (`DELETE`, lógico) con confirmación en la página; **reactivar** con `PUT is_active=true`
  sobre los datos actuales. El inventario y los pedidos existentes se conservan.
- **Inventario**: se distinguen registro con unidades, registro con 0 unidades, producto sin registro
  (404 de inventory; el primer ajuste positivo lo crea) e inventario no disponible. Los ajustes son
  **relativos** (agregar o quitar), nunca «fijar en N».
- **Ajustes idempotentes**: usuario, producto, delta, motivo e `Idempotency-Key` se guardan en
  `sessionStorage` antes de enviar (`ecommerce-uce.pending-adjustments`). Un resultado incierto se
  repite con la misma clave y el mismo cuerpo, también tras recargar; mientras no se resuelve no se
  puede iniciar otro ajuste de ese producto. «No se aplicó» solo ante respuestas definitivas de
  inventory-service (409 sin unidades, 404, 422, 403 y su propio 503 cuando product-service no
  responde). Cualquier otro 503, el 502/504 del gateway, un 500 o un error de red quedan inciertos.
  Un 409 «clave usada con otros datos» conserva el intento hasta que el administrador lo descarta
  tras revisar el historial.

## Administración de usuarios, operaciones y notificaciones

- **Pérdida de permisos**: un 403 en cualquier página de administración vuelve a consultar
  `GET /api/users/me`. Si la cuenta ya no es administradora, la sesión se cierra con «Tus permisos
  cambiaron» y no se hacen más llamadas administrativas; si lo sigue siendo, la página se recarga.
- **Usuarios** (`GET /api/users?limit=20&offset=`, `PATCH /api/users/{id}`): user-service no
  devuelve total ni permite buscar (limitación del MVP): se recorre por páginas y «Siguiente» se
  activa cuando la página llega llena. Activar/desactivar y cambiar el rol piden confirmación en la
  página y explican la decisión D11 (el token sigue valiendo hasta 30 min en los demás servicios). La
  propia cuenta no tiene acciones. Un resultado incierto nunca se repite: «Comprobar estado» lee la
  cuenta con `GET /api/users?ids=` y muestra su estado actual sin atribuirlo a esta solicitud.
- **Operaciones pendientes** (`GET /api/admin/operations`, `POST …/{id}/retry`): filtros en la URL
  (estado, tipo, tipo de error, pedido), 20 por página con total, bloqueo vigente («en proceso») y
  diagnóstico desplegable. Solo se ofrece reintentar operaciones `FAILED` sin bloqueo; las
  `PERMANENT` piden confirmación. order-service decide: un 409 `OPERATION_NOT_RETRYABLE` es
  definitivo. Un reintento incierto no se repite: se leen otra vez las operaciones de ese pedido.
  El diagnóstico (`payload`, `last_error`) se muestra como texto, con campos de aspecto secreto y
  tokens ocultos y longitud limitada.
- **Notificaciones de clientes** (`GET /api/notifications` con token de administrador): solo
  lectura; «leída» es el estado del cliente. Nombres con una sola consulta `?ids=` por página
  (`useBuyers`), con «Cliente #ID» si user-service falla.

## Estructura

| Carpeta | Contenido |
|---|---|
| `src/api/` | Cliente HTTP (`http.ts`), errores (`errors.ts`), tipos de los contratos reales (`types.ts`) y llamadas por servicio (`auth.ts`, `users.ts`, `products.ts`, `inventory.ts`, `cart.ts`, `orders.ts`, `notifications.ts`, `adminOperations.ts`) |
| `src/auth/` | Sesión (`session.ts`), contexto (`AuthContext.tsx`), guardias de rutas (`guards.tsx`) y validación del registro |
| `src/cart/` | Contexto del carrito (`CartContext.tsx`), clasificación de errores y validación de cantidades |
| `src/orders/` | Checkout (`useCheckout.ts`), seguimiento de un pedido (`useOrderTracker.ts`), intentos pendientes (`pendingOps.ts`), compradores (`useBuyers.ts`), estados y errores |
| `src/notifications/` | Contador de no leídas (`NotificationsContext.tsx`) |
| `src/admin/` | Formulario de producto y sus reglas, comprobación de creación incierta, panel de inventario, ajustes pendientes, clasificación de errores, revalidación tras un 403 y diagnóstico seguro |
| `src/catalog/` | Parámetros de la URL, disponibilidad y etiquetas de categorías |
| `src/components/` | Layout y componentes: botón, panel, aviso, indicador de carga, estado vacío, etiqueta de estado, tarjeta de producto, disponibilidad, cantidad y paginación |
| `src/pages/` | Páginas (`src/pages/admin/`: productos, usuarios, operaciones y notificaciones del administrador) |
| `src/styles/` | Variables de diseño, estilos base y de componentes (CSS propio) |
| `src/utils/` | Formato de moneda y fechas (es-EC) y comparación exacta de importes |
| `src/test/` | Utilidades de las pruebas y su configuración común (`setup.ts`: esperas de hasta 3 s); no se incluyen en la compilación |
| `Dockerfile`, `.dockerignore` | Imagen multietapa (Node compila, Nginx sin privilegios sirve) |
| `nginx/default.conf` | Servidor estático: navegación SPA, caché de `/assets/` y `/healthz` |
