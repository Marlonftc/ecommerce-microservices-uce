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

Cuando el frontend se sirva desde Docker (etapa 6), cambiar este valor exige reconstruir la imagen.
Ocultar las opciones **no es una restricción de seguridad**: es solo una decisión de la interfaz;
order-service acepta cualquiera de los tres modos válidos de cualquier usuario autenticado. Un intento
de pago ya iniciado conserva su modo original aunque el modo demostración se desactive.

## Comprobaciones

```powershell
npm run typecheck   # TypeScript estricto
npm run test        # Vitest
npm run build       # compilación de producción en dist/
```

## Sesión y roles

| Ruta | Acceso |
|---|---|
| `/` | Pública |
| `/productos`, `/productos/:id` | Públicas; agregar al carrito pide iniciar sesión |
| `/login`, `/registro` | Solo sin sesión; con sesión se redirige |
| `/cuenta`, `/carrito`, `/checkout` | Cualquier usuario con sesión (cliente o administrador) |
| `/pedidos`, `/pedidos/:id` | Con sesión. Un administrador ve los pedidos de todos, pero solo paga o cancela los suyos |
| `/admin` | Solo administradores; un cliente ve una página 403 |

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

## Estructura

| Carpeta | Contenido |
|---|---|
| `src/api/` | Cliente HTTP (`http.ts`), errores (`errors.ts`), tipos de los contratos reales (`types.ts`) y llamadas por servicio (`auth.ts`, `products.ts`, `inventory.ts`, `cart.ts`, `orders.ts`) |
| `src/auth/` | Sesión (`session.ts`), contexto (`AuthContext.tsx`), guardias de rutas (`guards.tsx`) y validación del registro |
| `src/cart/` | Contexto del carrito (`CartContext.tsx`), clasificación de errores y validación de cantidades |
| `src/orders/` | Checkout (`useCheckout.ts`), seguimiento de un pedido (`useOrderTracker.ts`), intentos pendientes (`pendingOps.ts`), estados y errores |
| `src/catalog/` | Parámetros de la URL, disponibilidad y etiquetas de categorías |
| `src/components/` | Layout y componentes: botón, panel, aviso, indicador de carga, estado vacío, etiqueta de estado, tarjeta de producto, disponibilidad, cantidad y paginación |
| `src/pages/` | Páginas |
| `src/styles/` | Variables de diseño, estilos base y de componentes (CSS propio) |
| `src/utils/` | Formato de moneda y fechas (es-EC) y comparación exacta de importes |
| `src/test/` | Utilidades de las pruebas (no se incluyen en la compilación) |
