import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Served by ai-memory at http://localhost:8765/ui  (base must match).
// `pnpm dev` (port 5180) proxies the API to the running server.
export default defineConfig({
    base: '/ui/',
    plugins: [react()],
    server: { port: 5180, proxy: { '/api': 'http://localhost:8765' } },
    build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 600 },
});
