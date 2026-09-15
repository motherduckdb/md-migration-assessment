# Local dashboard for an `md-assess` collection

A fully client-side, coordinated-views dashboard over the `report.*` facts that
`md-assess collect` writes for a Snowflake source. Built with [Mosaic](https://idl.uw.edu/mosaic/)
(`@uwdata/mosaic-core` + `@uwdata/vgplot`) running DuckDB-WASM in the browser.
There is no server, API or backend: every query runs in the browser against
parquet files exported from your `assessment.duckdb` and shipped inside the bundle.

## ⚠️ Local-only. Do not deploy.

The built bundle embeds the real database, schema, table, warehouse and tool
names of the assessed account. It is meant to be served from **localhost only**:
no CDN, no GitHub Pages, no object storage, no telemetry. Nothing is fetched
from a remote origin at runtime, and the app works with the network disconnected
on a cold cache. This folder's `.gitignore` excludes every derived artefact
(`public/data/*.parquet`, `public/extensions/`, `dist/`), so a clone never
carries collection data.

## Build and run

Requires Node 20+ and Python 3.10+ with the `duckdb` package (already a dependency
of `md-assess`, so `uv run` works from the repo root).

```bash
cd dashboard
npm install                                   # once (network)
npm run vendor                                # once: vendors the DuckDB parquet extension into public/ (network)
python3 scripts/export.py --db ../assessment.duckdb   # report.* + meta.* → public/data/*.parquet
npm run build                                 # → dist/
npx serve dist                                # any local static server; DuckDB-WASM cannot run from file://
```

`npm run dev` starts the Vite dev server for development. Re-running the export
is a single command and never mutates the source database (it is opened read-only).

## What it shows

One page, six coordinated sections, all sharing Mosaic Selections (a warehouse
menu, a date brush over the history window, a database menu, click-to-drill on
storage bars, an interactive warehouse legend):

1. **Storage footprint**: active / time-travel / fail-safe / clone bytes per
   database, stacked, sorted by total; click a bar to drill to schema grain.
   Fail-safe vanishes on migration, so it is never folded into one total.
2. **Spend over time**: daily credits per warehouse (Snowflake credits, never
   dollars: no rate is recorded). Brushing filters the workload and concurrency views.
3. **Workload profile**: (warehouse × query type) dots, bytes scanned vs
   server-side elapsed, sized by query count, faceted by class so transformations
   and file operations never blur together.
4. **Concurrency and contention**: hourly peak vs average concurrent queries,
   with queued-overload time per day aligned underneath.
5. **Migration-risk panel**: dialect-construct match rates beside the feature
   inventory grouped by category.
6. **Ingestion and client tools**: bytes loaded per database by load method, and
   client-tool fingerprints.

A sticky **coverage strip** summarises `meta.extract_runs` (complete / failed /
edition-limited) so the dashboard can never be read as complete when it isn't.

## The collector's contract, honoured in the UI

- **System objects are excluded by default.** Storage views read `NOT is_system`
  unless the explicit "show Snowflake system objects" toggle is on.
- **Missing evidence is not zero.** The feature inventory renders three distinct
  states: solid bar (`observed`, with `≥` when `lower_bound`), dashed outline with
  a zero tick and the words "0 observed" (`observed_zero`), and a full-width hatched
  grey bar with `unknown_reason` on hover (`unknown`). Extractors that completed
  with 0 rows from an Enterprise-only *activity* source (`table_read_heat` over
  `ACCESS_HISTORY`) are reported as unknown, never as "no reads"; Enterprise-only
  *object* catalogs (masking / row-access policies, tags) with 0 rows are real
  zeros on a non-Enterprise account and are labelled as such.

## What is exported

`scripts/export.py` reads `report.*` and `meta.*` only. Nothing from `raw.*` is
read or shipped (`raw.procedures` holds full stored-procedure bodies; `raw.*` is
unsummarised evidence). `tests/test_dashboard_export.py` builds a synthetic
collection with a sentinel in `raw.procedures` and asserts it never reaches a parquet.

| parquet | source | grain |
|---|---|---|
| `sizing` | `report.sizing` | table (`is_system` kept) |
| `spend_profile` | `report.spend_profile` | warehouse × day |
| `workload_rollup` | `report.workload_rollup` | warehouse × query_type × day (+ derived `query_class`) |
| `concurrency_profile` | `report.concurrency_profile` | warehouse × hour (+ `usage_date` so the date brush applies) |
| `dialect_constructs` | `report.dialect_constructs` | construct |
| `feature_inventory` | `report.feature_inventory` | feature (observation_status, lower_bound, unknown_reason preserved) |
| `ingestion_inventory` | `report.ingestion_inventory` | table |
| `tool_fingerprints` | `report.tool_fingerprints` | tool |
| `extract_runs`, `gaps`, `collection` | `meta.*` | extractor / collection |

For a 30-day standard-profile collection of a mid-sized account the parquet
totals about 1 MB; the built `dist/` is about 38 MB, almost all of it the DuckDB
wasm binary.

## Architecture notes

- `wasmConnector({ duckdb })` receives a DuckDB-WASM instance created from
  **vendored** assets (`src/duckdb.js`): the single-threaded `eh` bundle, imported
  through Vite `?url` so the `.wasm` and worker land in `dist/assets/`. No
  `SharedArrayBuffer`, no COOP/COEP requirement, no jsDelivr.
- DuckDB-WASM does not link the parquet reader statically; by default it fetches
  the extension from `extensions.duckdb.org` at runtime. `scripts/vendor-extensions.sh`
  downloads the exact matching extension at build time into `public/extensions/`
  and the app sets `custom_extension_repository` to that local folder with
  `autoinstall_known_extensions` / `autoload_known_extensions` off. `npm run build`
  refuses to build if the vendored file is missing.
- Parquet files are fetched relative to the page, registered with
  `registerFileBuffer`, then loaded with vgplot's `loadParquet`, so no reliance on
  range requests or on the worker resolving relative URLs. `vite.config.js` sets
  `base: './'` so `dist/` runs from any local path.
- Cross-view coordination is Mosaic Selections, not JS filtering: `$wh`
  (crossfilter: warehouse menu, date brush, legend) for warehouse-grain tables;
  `$db` (crossfilter: database menu, storage-bar toggle) for database-grain
  tables; two Params bound to the storage marks' source table implement the
  system-objects toggle.
- The storage bars use Plot's `title` channel for hover text rather than `tip`:
  Plot's tip pointer handler stops pointerdown propagation and would block the
  click-to-drill toggle.
- The coverage strip and feature inventory are custom `MosaicClient`s
  (`src/clients/`) because their encodings are not charts; they still query
  through the coordinator.
