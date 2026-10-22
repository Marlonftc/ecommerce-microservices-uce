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
| `/login`, `/registro` | Solo sin sesión; con sesión se redirige |
| `/cuenta` | Cualquier usuario con sesión |
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

## Estructura

| Carpeta | Contenido |
|---|---|
| `src/api/` | Cliente HTTP (`http.ts`), errores (`errors.ts`), tipos de los contratos reales (`types.ts`) y llamadas de autenticación (`auth.ts`) |
| `src/auth/` | Sesión (`session.ts`), contexto (`AuthContext.tsx`), guardias de rutas (`guards.tsx`) y validación del registro |
| `src/components/` | Layout y componentes base: botón, panel, aviso, indicador de carga, estado vacío, etiqueta de estado |
| `src/pages/` | Páginas |
| `src/styles/` | Variables de diseño, estilos base y de componentes (CSS propio) |
| `src/utils/` | Formato de moneda y fechas (es-EC) |
| `src/test/` | Utilidades de las pruebas (no se incluyen en la compilación) |
