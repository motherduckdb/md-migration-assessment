import * as vg from '@uwdata/vgplot';
import { createDuckDB, registerParquet, assetUrl } from './duckdb.js';
import { makePalette } from './palette.js';
import { HeaderClient } from './clients/header.js';
import { CoverageClient } from './clients/coverage.js';
import { FeatureInventoryClient } from './clients/features.js';
import { el, rows } from './clients/util.js';
import * as views from './views.js';
import './styles.css';

// One parquet per view-backing table, exported by scripts/export.sh.
const TABLES = [
  'sizing', 'spend_profile', 'workload_rollup', 'concurrency_profile',
  'dialect_constructs', 'feature_inventory', 'ingestion_inventory',
  'tool_fingerprints', 'extract_runs', 'gaps', 'collection'
];

async function main() {
  const boot = document.getElementById('boot');
  const status = msg => { if (boot) boot.textContent = msg; };

  // --- database: DuckDB-WASM in the browser, all assets vendored --------------
  const db = await createDuckDB();
  const connector = vg.wasmConnector({ duckdb: db });
  const coordinator = vg.coordinator();
  coordinator.databaseConnector(connector);

  // DuckDB-WASM ships parquet as a loadable extension and would otherwise fetch it
  // from extensions.duckdb.org at runtime. Point it at the vendored copy in the
  // bundle instead (scripts/vendor-extensions.sh) and forbid any auto-fetch.
  status('Loading vendored parquet extension…');
  await coordinator.exec([
    'SET autoinstall_known_extensions = false',
    'SET autoload_known_extensions = false',
    `SET custom_extension_repository = '${assetUrl('extensions')}'`,
    'INSTALL parquet',
    'LOAD parquet'
  ]);

  status('Loading parquet…');
  let bytes = 0;
  for (const t of TABLES) {
    bytes += await registerParquet(db, `${t}.parquet`, `data/${t}.parquet`);
  }
  await coordinator.exec(TABLES.map(t => vg.loadParquet(t, `${t}.parquet`)));

  // Storage views. Bytes are unpivoted into one row per (entity, component) so
  // active / time-travel / fail-safe / clone stack, then pre-aggregated at database
  // and schema grain with a per-entity total (for sorting) and a hover label.
  // Each exists twice: user objects only (default) and everything including the
  // SNOWFLAKE shared database; the system-objects toggle switches the source table
  // through a Param bound to the marks.
  const unpivot = `
    SELECT table_catalog, table_schema, is_system, component, bytes
    FROM sizing
    UNPIVOT (bytes FOR component IN (
      active_bytes AS active,
      time_travel_bytes AS time_travel,
      failsafe_bytes AS failsafe,
      retained_for_clone_bytes AS retained_for_clone))
    WHERE bytes IS NOT NULL`;
  const componentLabel = `CASE component
      WHEN 'active' THEN 'active' WHEN 'time_travel' THEN 'time travel'
      WHEN 'failsafe' THEN 'fail-safe (not migrated)' ELSE 'retained for clones' END`;
  const dbView = where => `
    SELECT table_catalog, component, bytes,
           sum(bytes) OVER (PARTITION BY table_catalog) AS total,
           table_catalog || ' · ' || ${componentLabel} || ': ' || fmt_bytes(bytes) AS label
    FROM (SELECT table_catalog, component, sum(bytes) AS bytes FROM (${unpivot}) ${where} GROUP BY 1, 2)`;
  const schemaView = where => `
    SELECT table_catalog, table_schema, table_catalog || '.' || table_schema AS schema_label, component, bytes,
           sum(bytes) OVER (PARTITION BY table_catalog, table_schema) AS total,
           table_catalog || '.' || table_schema || ' · ' || ${componentLabel} || ': ' || fmt_bytes(bytes) AS label
    FROM (SELECT table_catalog, table_schema, component, sum(bytes) AS bytes FROM (${unpivot}) ${where} GROUP BY 1, 2, 3)`;
  await coordinator.exec([
    // binary-scale byte formatter (1 GB = 1024^3 bytes), matching the axes
    `CREATE OR REPLACE MACRO fmt_bytes(b) AS CASE
       WHEN b >= 1099511627776 THEN round(b / 1099511627776, 1) || ' TB'
       WHEN b >= 1073741824 THEN round(b / 1073741824, 1) || ' GB'
       WHEN b >= 1048576 THEN round(b / 1048576, 1) || ' MB'
       WHEN b >= 1024 THEN round(b / 1024, 1) || ' KB'
       ELSE b || ' B' END`,
    `CREATE OR REPLACE VIEW sizing_db_all AS ${dbView('')}`,
    `CREATE OR REPLACE VIEW sizing_db_user AS ${dbView('WHERE NOT is_system')}`,
    `CREATE OR REPLACE VIEW sizing_schema_all AS ${schemaView('')}`,
    `CREATE OR REPLACE VIEW sizing_schema_user AS ${schemaView('WHERE NOT is_system')}`
  ]);

  // menu source: user databases only (system database lives behind the toggle)
  await coordinator.exec(`
    CREATE OR REPLACE VIEW databases AS
    SELECT DISTINCT table_catalog FROM sizing WHERE NOT is_system ORDER BY 1
  `);

  // --- stable colour domains ordered by importance ---------------------------
  const wh = rows(await coordinator.query(
    `SELECT warehouse_name FROM spend_profile GROUP BY 1 ORDER BY sum(credits_used) DESC, 1`));
  const dbs = rows(await coordinator.query(
    `SELECT table_catalog FROM sizing WHERE NOT is_system GROUP BY 1 ORDER BY sum(active_bytes) DESC NULLS LAST, 1`));
  const palette = makePalette(wh.map(r => r.warehouse_name), dbs.map(r => r.table_catalog));

  // history window from the data: [first day, day after last day) so the last bar fits
  const [{ start, end }] = rows(await coordinator.query(
    `SELECT min(usage_date) AS start, max(usage_date) + INTERVAL 1 DAY AS "end" FROM spend_profile`));
  const window = [new Date(start), new Date(end)];

  // --- shared selections and params -----------------------------------------
  // $wh: warehouse menu + spend-timeline brush + legend toggles (crossfilter: a
  //      plot never filters itself by its own brush). Applied to every table that
  //      has warehouse_name + usage_date.
  // $db: database menu + storage-bar toggle. Applied to every table that has
  //      table_catalog.
  // $sizingDb / $sizingSchema: which storage views the marks read (user objects by default).
  const $wh = vg.Selection.crossfilter();
  const $db = vg.Selection.crossfilter();
  const $sizingDb = vg.Param.value('sizing_db_user');
  const $sizingSchema = vg.Param.value('sizing_schema_user');
  const setSystem = show => {
    $sizingDb.update(show ? 'sizing_db_all' : 'sizing_db_user');
    $sizingSchema.update(show ? 'sizing_schema_all' : 'sizing_schema_user');
  };
  // local debugging aid: inspect selection state from the devtools console
  window.__assessment = { coordinator, $wh, $db, $sizingDb, $sizingSchema };

  // --- layout ----------------------------------------------------------------
  const app = document.getElementById('app');
  const W2 = 620;   // half-width plots
  const W1 = 1280;  // full-width plots

  const header = el('header', { class: 'header' });
  const coverage = el('div', { class: 'coverage' });

  const whMenu = vg.menu({ label: 'Warehouse', as: $wh, from: 'spend_profile', column: 'warehouse_name' });
  const dbMenu = vg.menu({ label: 'Database', as: $db, from: 'databases', column: 'table_catalog' });
  const sysToggle = el('label', { class: 'toggle' },
    el('input', { type: 'checkbox', onchange: e => setSystem(e.target.checked) }),
    ' show Snowflake system objects (SNOWFLAKE shared database)');
  const clear = el('button', { class: 'btn', text: 'Clear all filters', onclick: () => {
    $wh.reset(); $db.reset();
    for (const s of [whMenu, dbMenu]) { const sel = s.querySelector('select'); if (sel) sel.selectedIndex = 0; }
    const cb = sysToggle.querySelector('input'); cb.checked = false; setSystem(false);
  } });
  const controls = el('div', { class: 'controls' }, whMenu, dbMenu, sysToggle, clear,
    el('span', { class: 'hint', text: 'Brush the spend timeline to filter by date; click a storage bar to drill into its schemas; click a legend swatch to toggle a warehouse.' }));

  const panel = (title, sub, ...content) =>
    el('section', { class: 'panel' }, el('h2', { text: title }), sub ? el('p', { class: 'sub', text: sub }) : null, ...content);

  const grid = el('main', { class: 'grid' });
  app.replaceChildren(header, coverage, controls, grid);

  // header + coverage clients (query via the coordinator)
  coordinator.connect(new HeaderClient(header));
  coordinator.connect(new CoverageClient(coverage));

  // 1. storage
  const storageDb = views.storageByDatabase({ $db, $sizing: $sizingDb, palette, width: W2 });
  const storageSchema = views.storageBySchema({ $db, $sizing: $sizingSchema, palette, width: W2 });
  // visible statement of the current database filter (menu and/or bar click)
  const dbNote = el('span', { class: 'sel-note', text: 'all user databases' });
  $db.addEventListener('value', () => {
    const vals = $db.clauses.flatMap(c => (c.value == null ? [] : [c.value].flat(2))).map(String);
    dbNote.textContent = vals.length ? `drilled into ${[...new Set(vals)].join(', ')}` : 'all user databases';
  });
  grid.append(panel('1 · Storage footprint',
    'Active vs time-travel vs fail-safe bytes per database (user objects only). Fail-safe is retained by Snowflake and disappears on migration; it is stacked separately so it is never folded into the migrated total. Click a bar to drill into schemas; click it again to clear.',
    el('div', { class: 'row' },
      el('div', {}, el('h3', { text: 'by database' }), storageDb),
      el('div', {}, el('h3', {}, 'by schema (top 18) · ', dbNote), storageSchema)),
    vg.colorLegend({ for: 'storage', columns: 4 })));

  // 2. spend
  const spend = views.spendOverTime({ $wh, palette, width: W1 - 220, window });
  grid.append(panel('2 · Spend over time',
    'Daily Snowflake credits per warehouse (compute + cloud services) over the history window. Brush to set the date range for the workload and concurrency views; the warehouse menu and legend filter every view below.',
    el('div', { class: 'row-legend' }, spend, vg.colorLegend({ for: 'spend', as: $wh, columns: 1 }))));

  // 3. workload
  grid.append(panel('3 · Workload profile',
    'Each dot is a (warehouse, query type) pair over the selected window: bytes scanned vs server-side elapsed time, sized by query count, faceted by class so transformations (CTAS, MERGE, …) and file operations (PUT / LIST / REMOVE) never blur together. Both axes are symlog so zero-byte file operations stay visible at the left edge.',
    views.workloadProfile({ $wh, palette, width: W1 })));

  // 4. concurrency
  grid.append(panel('4 · Concurrency and contention',
    'Hourly peak (solid) against hourly average (dashed) concurrent queries per warehouse, with total queued-overload time per day from the workload rollup underneath, aligned on the same time axis, so contention sits next to the bursts that cause it.',
    views.concurrencyPeaks({ $wh, palette, width: W1, window }),
    views.queuedOverload({ $wh, palette, width: W1, window })));

  // 5. migration risk
  const features = el('div', { class: 'features' });
  grid.append(panel('5 · Migration-risk panel',
    'Left: Snowflake-specific SQL constructs as a share of the queries the server-side heuristic scanned (query text was never collected; these are frequency signals). Right: feature inventory by category with the collector\'s observed / observed-zero / unknown states preserved.',
    el('div', { class: 'row risk' },
      el('div', {}, el('h3', { text: 'dialect constructs' }), views.dialectMatchRate({ width: W2 })),
      el('div', {}, el('h3', { text: 'feature inventory' }), features))));
  coordinator.connect(new FeatureInventoryClient(features));

  // 6/7. bonus panels: ingestion + tools
  grid.append(panel('6 · Ingestion and client tools',
    'Left: bytes loaded per database in the window by load method (follows the database selection). Right: client-tool fingerprints from session metadata, ranked by query events.',
    el('div', { class: 'row' },
      el('div', {}, el('h3', { text: 'ingestion inventory' }), views.ingestionByDatabase({ $db, palette, width: W2 })),
      el('div', {}, el('h3', { text: 'tool fingerprints (top 14)' }), views.toolFingerprints({ width: W2 })))));

  const foot = el('footer', { class: 'foot' },
    `Loaded ${(bytes / 1024 / 1024).toFixed(2)} MB of parquet into DuckDB-WASM (single-threaded bundle). All queries run in this browser; nothing leaves this machine.`);
  app.append(foot);
  boot?.remove();
}

main().catch(err => {
  console.error(err);
  const boot = document.getElementById('boot');
  if (boot) {
    boot.className = 'boot error';
    boot.textContent = `Failed to start: ${err?.message ?? err}. This app must be served over http from a local static server (not file://).`;
  }
});
