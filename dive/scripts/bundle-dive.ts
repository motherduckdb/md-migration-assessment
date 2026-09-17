/**
 * Bundle the Dive source into the single file `md-assess publish` uploads.
 *
 * Mirrors dive-sandbox's infra/build.ts: everything under src/ is inlined,
 * the runtime-provided libraries stay external, JSX is preserved because the
 * Dive runtime compiles it.
 */
import * as esbuild from 'esbuild';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const out = resolve(root, '.build', 'index.tsx');

const EXTERNALS = ['react', 'react-dom', 'react-dom/client', '@motherduck/react-sql-query', 'recharts', 'd3', 'lucide-react'];

mkdirSync(dirname(out), { recursive: true });
esbuild.buildSync({
  entryPoints: [resolve(root, 'src', 'index.tsx')],
  bundle: true,
  outfile: out,
  jsx: 'preserve',
  minify: false,
  format: 'esm',
  external: EXTERNALS,
  logLevel: 'info',
});

const source = readFileSync(out, 'utf-8');
// esbuild hoists exports into one `export { X as default, REQUIRED_DATABASES }` statement.
const hasDefault = /export\s+default\s/.test(source) || /export\s*\{[^}]*\bas\s+default\b[^}]*\}/.test(source);
const hasRequired = /export\s+const\s+REQUIRED_DATABASES/.test(source) || /export\s*\{[^}]*\bREQUIRED_DATABASES\b[^}]*\}/.test(source);
if (!hasDefault) throw new Error('bundled Dive is missing a default export');
if (!hasRequired) throw new Error('bundled Dive is missing REQUIRED_DATABASES');
// Also drop a copy next to the Python package so `md-assess publish` can ship it.
const pkgCopy = resolve(root, '..', 'src', 'md_migration_assessment', 'dashboard', 'dive.tsx');
writeFileSync(pkgCopy, source);
console.log(`bundled Dive -> ${out} (${(source.length / 1024).toFixed(1)} KB) and ${pkgCopy}`);
