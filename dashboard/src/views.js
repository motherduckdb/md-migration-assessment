import * as vg from '@uwdata/vgplot';
import { formatBytes, formatCredits, formatInt, formatMs, formatPct } from './format.js';

const { sql, sum, max, avg, count } = vg;

const bytesTicks = v => formatBytes(v, 0);

// ---------------------------------------------------------------------------
// 1. Storage footprint (database grain, drill to schema on selection)
// ---------------------------------------------------------------------------
export function storageByDatabase({ $db, $sizing, palette, width }) {
  const { storage } = palette;
  // No Plot `tip` here: its pointer handler swallows pointerdown and would block
  // the click-to-drill toggle. Hover text comes from the SQL `label` column instead.
  return vg.plot(
    vg.barX(vg.from($sizing, { filterBy: $db }), {
      x: 'bytes',
      y: 'table_catalog',
      fill: 'component',
      title: 'label',
      channels: { total: 'total' },
      order: storage.domain,
      sort: { y: '-total' }
    }),
    vg.toggleY({ as: $db }),
    vg.name('storage'),
    vg.colorDomain(storage.domain), vg.colorRange(storage.range),
    vg.colorTickFormat(d => storage.labels[d] ?? d),
    vg.xLabel('bytes (binary scale) →'), vg.xTickFormat(bytesTicks), vg.xTicks(5),
    vg.yLabel(null), vg.yTickFormat(d => d),
    vg.marginLeft(195), vg.marginRight(20), vg.width(width), vg.height(420),
    vg.xGrid(true)
  );
}

export function storageBySchema({ $db, $sizing, palette, width }) {
  const { storage } = palette;
  return vg.plot(
    vg.barX(vg.from($sizing, { filterBy: $db }), {
      x: 'bytes',
      y: 'schema_label',
      fill: 'component',
      title: 'label',
      channels: { total: 'total' },
      order: storage.domain,
      sort: { y: '-total', limit: 18 }
    }),
    vg.colorDomain(storage.domain), vg.colorRange(storage.range),
    vg.xLabel('bytes (binary scale) →'), vg.xTickFormat(bytesTicks), vg.xTicks(5),
    vg.yLabel(null), vg.yTickFormat(d => (d.length > 40 ? '…' + d.slice(-39) : d)),
    vg.marginLeft(290), vg.marginRight(20), vg.width(width), vg.height(420),
    vg.xGrid(true)
  );
}

// ---------------------------------------------------------------------------
// 2. Spend over time — daily credits per warehouse; brush drives $wh
// ---------------------------------------------------------------------------
export function spendOverTime({ $wh, palette, width, window }) {
  const { warehouse } = palette;
  return vg.plot(
    vg.areaY(vg.from('spend_profile', { filterBy: $wh }), {
      x: 'usage_date',
      y: sum('credits_used'),
      fill: 'warehouse_name',
      order: warehouse.domain,
      tip: { format: { y: formatCredits, x: true } }
    }),
    vg.intervalX({ as: $wh, brush: { fill: 'none', stroke: '#333' } }),
    vg.name('spend'),
    vg.colorDomain(warehouse.domain), vg.colorRange(warehouse.range),
    vg.xDomain(window), vg.xLabel(null),
    vg.yLabel('↑ credits / day'), vg.yGrid(true),
    vg.marginLeft(50), vg.marginRight(20), vg.width(width), vg.height(280)
  );
}

// ---------------------------------------------------------------------------
// 3. Workload profile — query_type × warehouse, faceted by query class
// ---------------------------------------------------------------------------
export function workloadProfile({ $wh, palette, width }) {
  const { warehouse, queryClass } = palette;
  return vg.plot(
    vg.dot(vg.from('workload_rollup', { filterBy: $wh }), {
      x: sum('sum_bytes_scanned'),
      y: sum('sum_elapsed_ms'),
      r: sum('n_queries'),
      fill: 'warehouse_name',
      fx: 'query_class',
      channels: { query_type: 'query_type' },
      fillOpacity: 0.75, stroke: 'white', strokeWidth: 0.5,
      tip: { format: {
        x: bytesTicks, y: formatMs, r: formatInt, fx: false,
        fill: true, query_type: true
      } }
    }),
    vg.colorDomain(warehouse.domain), vg.colorRange(warehouse.range),
    vg.fxDomain(queryClass.domain),
    vg.fxLabel(null),
    vg.xScale('symlog'), vg.xLabel('bytes scanned in window (binary scale) →'),
    vg.xTickFormat(v => (v === 0 ? '0' : bytesTicks(v))),
    vg.xTicks([0, 2 ** 30, 2 ** 35, 2 ** 40, 2 ** 45]),
    vg.yScale('symlog'), vg.yLabel('↑ server-side elapsed in window'),
    vg.yTickFormat(v => (v === 0 ? '0' : formatMs(v))),
    vg.yTicks([0, 6e4, 3.6e6, 8.64e7, 8.64e8, 8.64e9]),
    vg.rRange([2, 26]),
    vg.xGrid(true), vg.yGrid(true),
    vg.marginLeft(70), vg.marginRight(20), vg.marginTop(30), vg.width(width), vg.height(360)
  );
}

// ---------------------------------------------------------------------------
// 4. Concurrency — hourly peak vs average, with queued-overload overlay
// ---------------------------------------------------------------------------
export function concurrencyPeaks({ $wh, palette, width, window }) {
  const { warehouse } = palette;
  return vg.plot(
    vg.lineY(vg.from('concurrency_profile', { filterBy: $wh }), {
      x: 'hour_start', y: 'avg_concurrent_queries', stroke: 'warehouse_name',
      strokeOpacity: 0.45, strokeWidth: 1, strokeDasharray: '2,2', curve: 'step'
    }),
    vg.lineY(vg.from('concurrency_profile', { filterBy: $wh }), {
      x: 'hour_start', y: 'peak_concurrent_queries', stroke: 'warehouse_name',
      strokeWidth: 1.2, curve: 'step',
      tip: { format: { y: formatInt, x: true } }
    }),
    vg.colorDomain(warehouse.domain), vg.colorRange(warehouse.range),
    vg.xDomain(window), vg.xLabel(null),
    vg.yLabel('↑ concurrent queries / hour (solid = peak, dashed = avg)'), vg.yGrid(true),
    vg.marginLeft(50), vg.marginRight(20), vg.width(width), vg.height(260)
  );
}

export function queuedOverload({ $wh, palette, width, window }) {
  const { warehouse } = palette;
  return vg.plot(
    vg.rectY(vg.from('workload_rollup', { filterBy: $wh }), {
      x1: 'usage_date',
      x2: sql`usage_date + INTERVAL 1 DAY`,
      y: sum('sum_queued_overload_ms'),
      fill: 'warehouse_name',
      order: warehouse.domain,
      inset: 0.5,
      tip: { format: { y: formatMs, x1: true, x2: false } }
    }),
    vg.colorDomain(warehouse.domain), vg.colorRange(warehouse.range),
    vg.xDomain(window), vg.xLabel(null),
    vg.yLabel('↑ queued (overload) time / day'), vg.yTickFormat(formatMs), vg.yGrid(true),
    vg.marginLeft(50), vg.marginRight(20), vg.width(width), vg.height(180)
  );
}

// ---------------------------------------------------------------------------
// 5a. Dialect constructs — match rate against queries scanned
// ---------------------------------------------------------------------------
export function dialectMatchRate({ width }) {
  return vg.plot(
    vg.barX(vg.from('dialect_constructs'), {
      x: sql`n_queries_matched::DOUBLE / NULLIF(n_queries_scanned, 0)`,
      y: 'construct',
      fill: sql`CASE WHEN n_queries_matched = 0 THEN 'observed zero' ELSE 'matched' END`,
      sort: { y: '-x' },
      channels: { matched: 'n_queries_matched', scanned: 'n_queries_scanned' },
      tip: { format: { x: formatPct, matched: formatInt, scanned: formatInt, fill: false } }
    }),
    vg.text(vg.from('dialect_constructs'), {
      x: sql`n_queries_matched::DOUBLE / NULLIF(n_queries_scanned, 0)`,
      y: 'construct',
      text: sql`CASE WHEN n_queries_matched = 0 THEN '0 matched' ELSE n_queries_matched::VARCHAR || ' matched' END`,
      textAnchor: 'start', dx: 4, fill: '#333', fontSize: 10
    }),
    vg.colorDomain(['matched', 'observed zero']), vg.colorRange(['#d1495b', '#9aa0a6']),
    vg.xLabel('share of scanned queries matching (server-side heuristic) →'),
    vg.xTickFormat(formatPct), vg.xGrid(true),
    vg.yLabel(null), vg.yTickFormat(d => d.replaceAll('_', ' ')),
    vg.marginLeft(160), vg.marginRight(90), vg.width(width), vg.height(260)
  );
}

// ---------------------------------------------------------------------------
// 7. Ingestion inventory (bonus panel) — bytes loaded per database by method
// ---------------------------------------------------------------------------
export function ingestionByDatabase({ $db, palette, width }) {
  const { loadMethod } = palette;
  return vg.plot(
    vg.barX(vg.from('ingestion_inventory', { filterBy: $db }), {
      x: sum('total_bytes_loaded'),
      y: 'table_catalog',
      fill: 'load_method',
      channels: { tables: count(), files: sum('total_files'), rows: sum('total_rows_loaded') },
      sort: { y: '-x' },
      tip: { format: { x: bytesTicks, tables: formatInt, files: formatInt, rows: formatInt } }
    }),
    vg.colorDomain(loadMethod.domain), vg.colorRange(loadMethod.range),
    vg.xLabel('bytes loaded in window (binary scale) →'), vg.xTickFormat(bytesTicks), vg.xTicks(5), vg.xGrid(true),
    vg.yLabel(null),
    vg.marginLeft(150), vg.marginRight(20), vg.width(width), vg.height(300)
  );
}

// ---------------------------------------------------------------------------
// 8. Tool fingerprints (bonus panel)
// ---------------------------------------------------------------------------
export function toolFingerprints({ width }) {
  return vg.plot(
    vg.barX(vg.from('tool_fingerprints'), {
      x: 'n_events',
      y: 'tool',
      fill: 'confidence',
      channels: { users: 'n_distinct_users', method: 'detection_method', elapsed: 'sum_elapsed_ms' },
      sort: { y: '-x', limit: 14 },
      tip: { format: { x: formatInt, users: formatInt, elapsed: formatMs } }
    }),
    vg.colorDomain(['high', 'medium', 'low']), vg.colorRange(['#3b6ea5', '#edae49', '#9aa0a6']),
    vg.xLabel('query events in window →'), vg.xTickFormat(formatInt), vg.xGrid(true),
    vg.yLabel(null),
    vg.marginLeft(170), vg.marginRight(20), vg.width(width), vg.height(300)
  );
}

