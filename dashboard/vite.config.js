import { defineConfig } from 'vite';

// Local-only static bundle. `base: './'` makes every asset URL relative so the
// dist/ folder can be served from any path on a local static server.
export default defineConfig({
  base: './',
  build: {
    target: 'esnext',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // never inline: the wasm and worker must be real files the worker/loader can fetch
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 4000
  },
  optimizeDeps: {
    exclude: ['@duckdb/duckdb-wasm']
  },
  worker: { format: 'es' },
  server: { host: '127.0.0.1' },
  preview: { host: '127.0.0.1' }
});
