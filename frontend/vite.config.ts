import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Development only: /api is forwarded to the gateway WITH its prefix, because the
// gateway itself routes /api/... to each service. The compiled app never contains a
// backend address: it always calls the relative path /api on its own origin.
// GATEWAY_URL is read by Node when Vite starts; it is not a VITE_* variable, so it is
// never embedded in the browser bundle.
const gatewayUrl = process.env.GATEWAY_URL ?? 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: gatewayUrl, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    // Longer limit for waitFor/findBy under load (see the file)
    setupFiles: ['src/test/setup.ts'],
  },
});
