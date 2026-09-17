/**
 * Collect license notices for the JavaScript bundled into the local-mode
 * dashboard and write them next to the bundle, so the wheel that ships the
 * minified code also ships the notices its licenses require.
 *
 * The closure is the production dependency graph of dive/package.json,
 * resolved the way Node does (nearest node_modules walking up). Every package
 * contributes name, version, SPDX identifier and the text of every license
 * file it ships, including ones vendored in subdirectories. A package with no
 * license text at all fails the build: an SPDX identifier alone is not a
 * notice, and shipping it would be a compliance gap, not a warning.
 *
 * The Dive source uploaded by `md-assess publish` bundles none of these (they
 * are externals provided by the MotherDuck runtime), so this file covers the
 * local bundle only.
 */
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const out = resolve(root, '..', 'src', 'md_migration_assessment', 'dashboard', 'static', 'THIRD_PARTY_NOTICES.txt');

type Pkg = { name: string; version: string; license?: string | { type?: string }; licenses?: Array<{ type?: string }>; dependencies?: Record<string, string>; peerDependencies?: Record<string, string> };

function readPkg(dir: string): Pkg {
  return JSON.parse(readFileSync(join(dir, 'package.json'), 'utf-8')) as Pkg;
}

/** Node resolution for a package directory: nearest node_modules walking up from `from`. */
function resolvePkgDir(name: string, from: string): string | null {
  let dir = from;
  for (;;) {
    const candidate = join(dir, 'node_modules', name);
    if (existsSync(join(candidate, 'package.json'))) return candidate;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

function licenseId(p: Pkg): string {
  if (typeof p.license === 'string') return p.license;
  if (p.license && typeof p.license === 'object' && p.license.type) return p.license.type;
  if (p.licenses?.length) return p.licenses.map((l) => l.type ?? '?').join(' OR ');
  return '';
}

const LICENSE_FILE = /^(licen[cs]e|copying)(\.|$)/i;

/**
 * Every license file in the package, including ones vendored in
 * subdirectories (victory-vendor ships d3 modules under lib-vendor/<pkg>/LICENSE).
 * Nested node_modules belong to other packages and are resolved separately.
 */
function licenseFiles(dir: string, rel = ''): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(join(dir, rel), { withFileTypes: true })) {
    const relPath = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
      found.push(...licenseFiles(dir, relPath));
    } else if (LICENSE_FILE.test(entry.name)) {
      found.push(relPath);
    }
  }
  return found.sort();
}

function licenseText(dir: string): string | null {
  const files = licenseFiles(dir);
  if (files.length === 0) return null;
  return files
    .map((f) => {
      const text = readFileSync(join(dir, f), 'utf-8').trim();
      return f.includes('/') ? `[${f}]\n${text}` : text;
    })
    .join('\n\n');
}

const rootPkg = readPkg(root);
const seen = new Map<string, { dir: string; pkg: Pkg }>();
const queue: Array<{ name: string; from: string }> = Object.keys(rootPkg.dependencies ?? {}).map((name) => ({ name, from: root }));
const missing: string[] = [];

while (queue.length) {
  const { name, from } = queue.shift()!;
  const dir = resolvePkgDir(name, from);
  if (!dir) {
    missing.push(`${name} (from ${from})`);
    continue;
  }
  const pkg = readPkg(dir);
  const key = `${pkg.name}@${pkg.version}`;
  if (seen.has(key)) continue;
  seen.set(key, { dir, pkg });
  for (const dep of Object.keys(pkg.dependencies ?? {})) queue.push({ name: dep, from: dir });
}
if (missing.length) throw new Error(`unresolved production dependencies:\n  ${missing.join('\n  ')}\nrun npm install in dive/`);

const entries = [...seen.values()].sort((a, b) => a.pkg.name.localeCompare(b.pkg.name) || a.pkg.version.localeCompare(b.pkg.version));
// An SPDX identifier alone is not a notice: every package must contribute the
// actual license text it ships, or the build fails.
const noText: string[] = [];
const sections: string[] = [];
for (const { dir, pkg } of entries) {
  const id = licenseId(pkg);
  const text = licenseText(dir);
  if (!text) {
    noText.push(`${pkg.name}@${pkg.version}${id ? ` (declares ${id})` : ''}`);
    continue;
  }
  sections.push(['-'.repeat(78), `${pkg.name} ${pkg.version}`, `License: ${id || '(see text below)'}`, '', text, ''].join('\n'));
}
if (noText.length) {
  throw new Error(
    `packages without license text (an SPDX declaration alone cannot ship):\n  ${noText.join('\n  ')}\n` +
      'add the license text to the notices source or drop the dependency',
  );
}

const summary = new Map<string, number>();
for (const { pkg } of entries) summary.set(licenseId(pkg) || '(text only)', (summary.get(licenseId(pkg) || '(text only)') ?? 0) + 1);

const header = [
  'THIRD-PARTY NOTICES for the md-assess dashboard bundle',
  '',
  'The files in this directory bundle the following open-source JavaScript',
  `packages (${entries.length} packages, the production dependency closure of dive/package.json).`,
  'Each package is distributed under its own license, reproduced below.',
  '',
  'Summary by license: ' + [...summary.entries()].sort().map(([k, v]) => `${k} (${v})`).join(', '),
  '',
  `Generated by dive/scripts/third-party-notices.ts on ${new Date().toISOString().slice(0, 10)}.`,
  '',
].join('\n');

writeFileSync(out, header + '\n' + sections.join('\n'));
console.log(`third-party notices: ${entries.length} packages -> ${out}`);
console.log('  ' + [...summary.entries()].sort().map(([k, v]) => `${k}: ${v}`).join(', '));
