import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';
import duckdbProxy from './runtime/duckdb-proxy-plugin.ts';

// One Vite project, two jobs:
//   dev   — preview the Dive against a local collection (ASSESSMENT_DB=path.duckdb
//           npm run dev). The proxy plugin answers api/query with Node DuckDB.
//   build — emit the local-mode bundle (runtime + Dive) into the Python package,
//           where `md-assess dashboard` serves it next to its own api/query.
// `base: './'` keeps every asset URL relative so the bundle works under the
// per-launch path prefix the Python server uses.
export default defineConfig({
  base: './',
  resolve: {
    alias: {
      '@motherduck/react-sql-query': resolve(import.meta.dirname, 'runtime/query-provider.tsx'),
    },
  },
  plugins: [react(), duckdbProxy()],
  server: { host: '127.0.0.1' },
  build: {
    outDir: resolve(import.meta.dirname, '..', 'src', 'md_migration_assessment', 'dashboard', 'static'),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
});
