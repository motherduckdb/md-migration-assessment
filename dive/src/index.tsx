/**
 * md-assess dashboard, authored as a MotherDuck Dive.
 *
 * The same source runs in two places:
 *   - as a Dive in MotherDuck, over the handoff database `md-assess publish` uploads;
 *   - locally under `md-assess dashboard`, whose runtime implements the
 *     `@motherduck/react-sql-query` hooks against a loopback query endpoint.
 *
 * Every table reference is fully qualified under the `assessment` alias so the
 * SQL is identical in both modes. Filter state lives in useDiveState so a
 * shared (or local) link reproduces the view.
 */
import { useMemo } from 'react';
import { useDiveState, useSQLQuery } from '@motherduck/react-sql-query';
import { N, S } from './format';
import { BG, MUTED, seriesColor } from './palette';
import * as Q from './queries';
import type { Filters } from './queries';
import { CoverageStrip } from './coverage';
import { StoragePanel } from './panels/storage';
import { SpendPanel, type DateRange } from './panels/spend';
import { WorkloadPanel } from './panels/workload';
import { ConcurrencyPanel } from './panels/concurrency';
import { RiskPanel } from './panels/risk';
import { IngestionPanel } from './panels/ingestion';
import { CSS, Skeleton, Warn } from './ui';

// `md-assess publish` rewrites the path to the uploaded database; the alias is
// what every query in ./queries.ts uses and must stay `assessment`.
export const REQUIRED_DATABASES = [
  {
    type: 'database',
    path: 'md:md_assessment',
    alias: 'assessment',
  },
];

function Header() {
  const coll = useSQLQuery(Q.collection());
  const win = useSQLQuery(Q.window());
  const c = (Array.isArray(coll.data) ? coll.data : [])[0] ?? {};
  const w = (Array.isArray(win.data) ? win.data : [])[0] ?? {};
  const account = [S(c.source_deployment), S(c.source_region)].filter(Boolean).join(' · ') || '(account not recorded)';
  return (
    <header>
      <h1 style={{ fontSize: 22, margin: '4px 0 6px', fontWeight: 600 }}>Snowflake → MotherDuck migration assessment</h1>
      {coll.isLoading && !coll.data ? (
        <Skeleton height={60} />
      ) : (
        <p style={{ margin: '0 0 8px', maxWidth: 1200 }}>
          Read-only dashboard over the <code>md-assess</code> {S(c.tool_version)} collection (profile {S(c.profile) || 'standard'}) of Snowflake account <strong>{account}</strong>
          {c.started_at ? <>, collected {S(c.started_at)}</> : null}. History window:{' '}
          <strong>{w.window_start ? `${S(w.window_start)} → ${S(w.window_end)}` : 'unknown'}</strong> ({N(c.history_days) || '?'} days requested). Every count excludes Snowflake system objects unless the system-objects toggle is on. Counts marked <strong>≥</strong> are lower bounds limited by what the collecting role could see. Missing evidence is never shown as zero:{' '}
          <span className="chip chip-unknown">unknown</span> is a distinct state from <span className="chip chip-zero">observed zero</span>. Credits are Snowflake credits (no rate is recorded, so no dollar figure is implied); latencies are server-side elapsed time.
        </p>
      )}
      <Warn>This dashboard names real databases, schemas, tables, warehouses and tools. Share it only with people entitled to see them.</Warn>
    </header>
  );
}

function MultiSelect({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap' }}>
      <span style={{ fontWeight: 600, marginRight: 4 }}>{label}</span>
      <button className={`btn${value.length === 0 ? ' on' : ''}`} onClick={() => onChange([])}>All</button>
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <button key={o} className={`btn${on ? ' on' : ''}`} onClick={() => onChange(on ? value.filter((v) => v !== o) : [...value, o])} title={on ? 'click to remove from the selection' : 'click to add to the selection'}>
            {o}
          </button>
        );
      })}
    </div>
  );
}

export default function AssessmentDashboard() {
  const [wh, setWh] = useDiveState<string[]>('wh', []);
  const [range, setRange] = useDiveState<DateRange>('range', null);
  const [db, setDb] = useDiveState<string | null>('db', null);
  const [sys, setSys] = useDiveState<boolean>('sys', false);
  const [drill, setDrill] = useDiveState<string | null>('drill', null);

  const filters: Filters = useMemo(
    () => ({ warehouses: wh ?? [], start: range?.start ?? null, end: range?.end ?? null, database: db, showSystem: Boolean(sys) }),
    [wh, range, db, sys],
  );

  // stable colour domains ordered by importance (credits), shared by every panel
  const whQuery = useSQLQuery(Q.warehouses());
  const warehouses = useMemo(() => (Array.isArray(whQuery.data) ? whQuery.data : []).map((r) => S(r.warehouse_name)), [whQuery.data]);
  const color = useMemo(() => seriesColor(warehouses), [warehouses]);
  const dbQuery = useSQLQuery(Q.databases(filters));
  const databases = (Array.isArray(dbQuery.data) ? dbQuery.data : []).map((r) => S(r.table_catalog));
  const win = useSQLQuery(Q.window());
  const w = (Array.isArray(win.data) ? win.data : [])[0] ?? {};
  const windowDays = range
    ? Math.max(1, Math.round((Date.parse(range.end) - Date.parse(range.start)) / 86400000) + 1)
    : N(w.window_days) || 30;

  const clearAll = () => {
    setWh(undefined);
    setRange(undefined);
    setDb(undefined);
    setSys(undefined);
    setDrill(undefined);
  };
  const anyFilter = (wh?.length ?? 0) > 0 || range != null || db != null || Boolean(sys) || drill != null;

  return (
    <div className="mda" style={{ background: BG, padding: '16px 20px 40px', minHeight: '100%' }}>
      <style>{CSS}</style>
      <div style={{ maxWidth: 1340, margin: '0 auto' }}>
        <Header />

        <div style={{ position: 'sticky', top: 0, zIndex: 5, background: '#fff', border: '1px solid #e5e5e5', borderRadius: 8, padding: '8px 12px', marginBottom: 10, boxShadow: '0 2px 6px rgba(0,0,0,.05)' }}>
          <CoverageStrip />
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 22px', alignItems: 'center', padding: '10px 12px', background: '#fff', border: '1px solid #e5e5e5', borderRadius: 8, marginBottom: 14 }}>
          <MultiSelect label="Warehouse" options={warehouses} value={wh ?? []} onChange={(v) => setWh(v.length ? v : undefined)} />
          <label style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <span style={{ fontWeight: 600 }}>Database</span>
            <select value={db ?? ''} onChange={(e) => { setDb(e.target.value || undefined); setDrill(undefined); }}>
              <option value="">All</option>
              {databases.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <input type="checkbox" checked={Boolean(sys)} onChange={(e) => setSys(e.target.checked || undefined)} /> show Snowflake system objects (SNOWFLAKE shared database)
          </label>
          <span style={{ color: MUTED, fontSize: 12 }}>
            {range ? <>dates <strong>{range.start} → {range.end}</strong></> : 'all dates'}
          </span>
          <button className="btn" onClick={clearAll} disabled={!anyFilter} style={{ opacity: anyFilter ? 1 : 0.5 }}>Clear all filters</button>
          <span style={{ color: MUTED, fontSize: 12, flexBasis: '100%' }}>
            Brush the spend timeline to filter by date; click a storage bar to drill into its schemas; warehouse buttons toggle a warehouse in and out of every panel.
          </span>
        </div>

        <main style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <StoragePanel filters={filters} drill={drill} setDrill={(v) => setDrill(v ?? undefined)} />
          <SpendPanel filters={filters} warehouses={warehouses} color={color} range={range} setRange={(r) => setRange(r ?? undefined)} />
          <WorkloadPanel filters={filters} color={color} />
          <ConcurrencyPanel filters={filters} warehouses={warehouses} color={color} windowDays={windowDays} />
          <RiskPanel />
          <IngestionPanel filters={filters} />
        </main>

        <footer style={{ marginTop: 18, color: MUTED, fontSize: 12 }}>
          Facts, not judgments: this dashboard shows what the collector observed and how well it could observe it. Compatibility ratings and effort estimates are out of scope.
        </footer>
      </div>
    </div>
  );
}
