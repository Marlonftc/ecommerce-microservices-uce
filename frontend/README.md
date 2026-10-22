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

## Estructura

| Carpeta | Contenido |
|---|---|
| `src/api/` | Cliente HTTP (`http.ts`), errores (`errors.ts`) y tipos de los contratos reales (`types.ts`) |
| `src/components/` | Layout y componentes base: botón, panel, aviso, indicador de carga, estado vacío, etiqueta de estado |
| `src/pages/` | Páginas |
| `src/styles/` | Variables de diseño, estilos base y de componentes (CSS propio) |
| `src/utils/` | Formato de moneda y fechas (es-EC) |
