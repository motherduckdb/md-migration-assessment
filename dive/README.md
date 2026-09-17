# The md-assess dashboard (a MotherDuck Dive)

`src/index.tsx` is the dashboard: a MotherDuck Dive over the `report.*` and
`meta.*` layers of an `md-assess` collection. The same source runs in two
places:

- **Locally**, via `md-assess dashboard --db assessment.duckdb`. The Python
  package serves the bundle built from this folder and answers its queries
  against the local file over loopback. Nothing leaves the machine, and the
  user needs no Node toolchain: the bundle ships inside the wheel.
- **In MotherDuck**, via `md-assess publish`, which uploads the reduced handoff
  database and creates the Dive from the bundled source in `.build/index.tsx`
  (`npm run bundle`).

## Develop

```sh
npm install
ASSESSMENT_DB=../assessment.duckdb npm run dev   # hot-reloading preview at http://127.0.0.1:5173
```

The dev server answers `api/query` with Node DuckDB over the file named by
`ASSESSMENT_DB`, attached read-only under the alias `assessment` (the alias the
Dive declares in `REQUIRED_DATABASES`). The collection must be on the current
meta and report schema; `md-assess assess --db …` rebuilds the report layer.

## Build

```sh
npm run typecheck
npm test          # runtime unit tests (node:test via tsx)
npm run build     # local-mode bundle + THIRD_PARTY_NOTICES.txt -> src/md_migration_assessment/dashboard/static/
npm run bundle    # Dive source -> .build/index.tsx and src/md_migration_assessment/dashboard/dive.tsx
```

Both outputs are gitignored and produced by CI and the release workflow;
`pyproject.toml` lists them as wheel artifacts. From a source checkout,
`md-assess dashboard` tells you to run the build if the bundle is missing.

## Layout

```
src/index.tsx        the Dive: default export + REQUIRED_DATABASES, filter state, layout
src/queries.ts       every SQL string, parameterised by the filter state
src/coverage.tsx     sticky coverage strip from meta.extract_runs
src/panels/*.tsx     one file per panel
src/format.ts        value helpers (N(), bytes, durations, pivot)
src/palette.ts       colours: MotherDuck series palette + contract colours
src/ui.tsx           Panel, Skeleton, TipBox, the one <style> block
runtime/             local mode only: entry point, query provider, dev proxy
scripts/bundle-dive.ts  esbuild -> .build/index.tsx (what publish uploads)
```

## Rules the source follows

- Only the Dive runtime's libraries: `react`, `recharts`, `d3`, `lucide-react`,
  `@motherduck/react-sql-query`. No other imports resolve in MotherDuck.
- Inline styles plus the single `<style>` element in `ui.tsx`. No Tailwind
  (local mode cannot fetch it), no external CSS or fonts.
- Every table reference is `"assessment"."report"."…"` or `"assessment"."meta"."…"`.
- Dates are formatted in SQL; every numeric value goes through `N()`.
- The collector's contract is kept: system objects excluded by default,
  `observed_zero` and `unknown` are never drawn alike, `lower_bound` counts
  carry `≥`, and the coverage strip is always visible.
