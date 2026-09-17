// Every SQL string the dashboard runs, in one place. All references are fully
// qualified under the `assessment` alias declared in REQUIRED_DATABASES, so the
// same text runs in MotherDuck and against the local file.
//
// Dates are formatted in SQL (strftime) so no DuckDB date object reaches JSX.
import { q } from './format';

export const DB = '"assessment"';
const R = (t: string) => `${DB}."report"."${t}"`;
const M = (t: string) => `${DB}."meta"."${t}"`;

export type Filters = {
  /** empty = all warehouses */
  warehouses: string[];
  /** inclusive YYYY-MM-DD bounds, null = open */
  start: string | null;
  end: string | null;
  /** database (table_catalog) or null = all */
  database: string | null;
  /** include Snowflake system objects (SNOWFLAKE shared database) */
  showSystem: boolean;
};

export const EMPTY_FILTERS: Filters = { warehouses: [], start: null, end: null, database: null, showSystem: false };

const whClause = (f: Filters, col = 'warehouse_name') =>
  f.warehouses.length ? ` AND ${col} IN (${f.warehouses.map(q).join(', ')})` : '';
const dateClause = (f: Filters, col = 'usage_date') =>
  (f.start ? ` AND ${col} >= DATE ${q(f.start)}` : '') + (f.end ? ` AND ${col} <= DATE ${q(f.end)}` : '');
const systemClause = (f: Filters) => (f.showSystem ? '' : ' AND NOT coalesce(is_system, false)');
const dbClause = (f: Filters, col = 'table_catalog') =>
  f.database ? ` AND coalesce(${col}, '(unattributed)') = ${q(f.database)}` : '';

// ── header and coverage ───────────────────────────────────────────────────────

export const collection = () => `
  SELECT tool_version, profile, history_days, source_kind, source_deployment, source_region, source_edition,
         strftime(started_at, '%Y-%m-%d %H:%M UTC') AS started_at
  FROM ${M('collections')}`;

export const window = () => `
  SELECT strftime(min(usage_date), '%Y-%m-%d') AS window_start,
         strftime(max(usage_date), '%Y-%m-%d') AS window_end,
         date_diff('day', min(usage_date), max(usage_date)) + 1 AS window_days
  FROM ${R('spend_profile')}`;

export const extractRuns = () => `
  SELECT extractor, target_table, status, source_used, rows_written, required_privilege, min_edition,
         error_category, left(error_detail, 300) AS error_detail
  FROM ${M('extract_runs')}
  ORDER BY extractor`;

// ── filter domains ────────────────────────────────────────────────────────────

export const warehouses = () => `
  SELECT warehouse_name, sum(credits_used) AS credits
  FROM ${R('spend_profile')}
  GROUP BY 1 ORDER BY 2 DESC, 1`;

export const databases = (f: Filters) => `
  SELECT coalesce(table_catalog, '(unattributed)') AS table_catalog, sum(active_bytes) AS active_bytes
  FROM ${R('sizing')}
  WHERE 1 = 1${systemClause(f)}
  GROUP BY 1 ORDER BY 2 DESC NULLS LAST, 1`;

// ── 1. storage footprint ──────────────────────────────────────────────────────

const STORAGE_SUMS = `
  sum(active_bytes) AS active, sum(time_travel_bytes) AS time_travel,
  sum(failsafe_bytes) AS failsafe, sum(retained_for_clone_bytes) AS retained_for_clone,
  sum(coalesce(active_bytes, 0) + coalesce(time_travel_bytes, 0) + coalesce(failsafe_bytes, 0)
      + coalesce(retained_for_clone_bytes, 0)) AS total,
  count(*) AS tables`;

export const storageByDatabase = (f: Filters) => `
  SELECT coalesce(table_catalog, '(unattributed)') AS name, ${STORAGE_SUMS}
  FROM ${R('sizing')}
  WHERE 1 = 1${systemClause(f)}${dbClause(f)}
  GROUP BY 1 ORDER BY total DESC NULLS LAST, 1`;

export const storageBySchema = (f: Filters, limit = 18) => `
  SELECT coalesce(table_catalog, '(unattributed)') || '.' || coalesce(table_schema, '(unattributed)') AS name,
         ${STORAGE_SUMS}
  FROM ${R('sizing')}
  WHERE 1 = 1${systemClause(f)}${dbClause(f)}
  GROUP BY 1 ORDER BY total DESC NULLS LAST, 1
  LIMIT ${limit}`;

// ── 2. spend over time ────────────────────────────────────────────────────────
// Not date-filtered on purpose: this chart carries the brush that sets the range.

export const spendDaily = (f: Filters) => `
  SELECT strftime(usage_date, '%Y-%m-%d') AS day, warehouse_name, sum(credits_used) AS credits
  FROM ${R('spend_profile')}
  WHERE 1 = 1${whClause(f)}
  GROUP BY 1, 2 ORDER BY 1, 2`;

// ── 3. workload profile ───────────────────────────────────────────────────────

export const QUERY_CLASS_SQL = `CASE
  WHEN query_type IN ('PUT_FILES','LIST_FILES','REMOVE_FILES','GET_FILES') THEN 'file operation'
  WHEN query_type IN ('CREATE_TABLE_AS_SELECT','MERGE','INSERT','UPDATE','DELETE',
                      'COPY','UNLOAD','TRUNCATE_TABLE','MULTI_TABLE_INSERT') THEN 'transformation'
  WHEN query_type IN ('SELECT','EXPLAIN','WITH') THEN 'read'
  WHEN query_type IN ('SHOW','DESCRIBE','USE','ALTER_SESSION','COMMIT','BEGIN_TRANSACTION',
                      'ROLLBACK','GET_RESULT','SET','UNSET') THEN 'metadata / session'
  ELSE 'ddl / admin' END`;

export const workloadPoints = (f: Filters) => `
  SELECT warehouse_name, query_type, ${QUERY_CLASS_SQL} AS query_class,
         sum(n_queries) AS n_queries, sum(sum_elapsed_ms) AS elapsed_ms, sum(sum_bytes_scanned) AS bytes_scanned,
         sum(n_spilled_remote) AS n_spilled_remote, sum(sum_queued_overload_ms) AS queued_ms
  FROM ${R('workload_rollup')}
  WHERE 1 = 1${whClause(f)}${dateClause(f)}
  GROUP BY 1, 2, 3
  HAVING sum(n_queries) > 0
  ORDER BY n_queries DESC`;

// ── 4. concurrency and contention ─────────────────────────────────────────────
// Hourly rows for a year are too many points for an SVG chart, so long windows
// are coarsened to a day in SQL; the grain is chosen by the caller from the window.

export type Grain = 'hour' | 'day';

export const concurrency = (f: Filters, grain: Grain) => `
  SELECT strftime(date_trunc('${grain}', hour_start), ${grain === 'hour' ? "'%Y-%m-%d %H:00'" : "'%Y-%m-%d'"}) AS t,
         warehouse_name,
         max(peak_concurrent_queries) AS peak, avg(avg_concurrent_queries) AS avg_c
  FROM ${R('concurrency_profile')}
  WHERE 1 = 1${whClause(f)}${dateClause(f, 'CAST(hour_start AS DATE)')}
  GROUP BY 1, 2 ORDER BY 1, 2`;

export const queuedOverload = (f: Filters) => `
  SELECT strftime(usage_date, '%Y-%m-%d') AS day, warehouse_name, sum(sum_queued_overload_ms) AS queued_ms
  FROM ${R('workload_rollup')}
  WHERE 1 = 1${whClause(f)}${dateClause(f)}
  GROUP BY 1, 2 ORDER BY 1, 2`;

// ── 5. migration-risk panel ───────────────────────────────────────────────────

export const dialectConstructs = () => `
  SELECT construct, n_queries_matched, n_queries_scanned,
         n_queries_matched::DOUBLE / NULLIF(n_queries_scanned, 0) AS match_rate, source, note
  FROM ${R('dialect_constructs')}
  ORDER BY match_rate DESC NULLS LAST, construct`;

export const featureInventory = () => `
  SELECT category, feature, observation_status, count, lower_bound, unknown_reason,
         array_to_string(sample_objects, ', ') AS sample_objects, source_extractor, note
  FROM ${R('feature_inventory')}
  ORDER BY category, feature`;

// ── 6. ingestion and client tools ─────────────────────────────────────────────

export const ingestion = (f: Filters) => `
  SELECT coalesce(table_catalog, '(unattributed)') AS name, load_method,
         sum(total_bytes_loaded) AS bytes_loaded, sum(total_files) AS files,
         sum(total_rows_loaded) AS rows_loaded, count(*) AS tables
  FROM ${R('ingestion_inventory')}
  WHERE 1 = 1${dbClause(f)}
  GROUP BY 1, 2 ORDER BY 1, 2`;

export const tools = (limit = 14) => `
  SELECT tool, detection_method, confidence, n_events, n_distinct_users, sum_elapsed_ms
  FROM ${R('tool_fingerprints')}
  ORDER BY n_events DESC NULLS LAST, tool
  LIMIT ${limit}`;

/** Every query, for tooling that wants to run them all against a collection. */
export const ALL_QUERIES = (f: Filters = EMPTY_FILTERS): Record<string, string> => ({
  collection: collection(),
  window: window(),
  extractRuns: extractRuns(),
  warehouses: warehouses(),
  databases: databases(f),
  storageByDatabase: storageByDatabase(f),
  storageBySchema: storageBySchema(f),
  spendDaily: spendDaily(f),
  workloadPoints: workloadPoints(f),
  concurrencyHour: concurrency(f, 'hour'),
  concurrencyDay: concurrency(f, 'day'),
  queuedOverload: queuedOverload(f),
  dialectConstructs: dialectConstructs(),
  featureInventory: featureInventory(),
  ingestion: ingestion(f),
  tools: tools(),
});
