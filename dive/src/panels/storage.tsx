// 1. Storage footprint: active / time-travel / fail-safe / clone bytes per
// database, stacked; click a bar to drill into that database's schemas.
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatBytes, formatInt } from '../format';
import { MUTED, PRIMARY, STORAGE } from '../palette';
import * as Q from '../queries';
import type { Filters } from '../queries';
import { ErrorNote, Panel, Row, Skeleton, TipBox } from '../ui';

type StorageRow = { name: string; active: number; time_travel: number; failsafe: number; retained_for_clone: number; total: number; tables: number };

function toRows(data: unknown): StorageRow[] {
  return (Array.isArray(data) ? data : []).map((r) => ({
    name: S(r.name),
    active: N(r.active),
    time_travel: N(r.time_travel),
    failsafe: N(r.failsafe),
    retained_for_clone: N(r.retained_for_clone),
    total: N(r.total),
    tables: N(r.tables),
  }));
}

const shortName = (s: string, max = 34) => (s.length > max ? '…' + s.slice(-(max - 1)) : s);

function StorageBars({ rows, height, onClick, labelWidth }: { rows: StorageRow[]; height: number; onClick?: (name: string) => void; labelWidth: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }} barCategoryGap={4}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" horizontal={false} />
        <XAxis type="number" tickFormatter={(v) => formatBytes(v, 0)} fontSize={11} tickCount={5} />
        <YAxis type="category" dataKey="name" width={labelWidth} fontSize={11} tickFormatter={(v) => shortName(String(v))} interval={0} />
        <Tooltip
          cursor={{ fill: 'rgba(7,119,179,.06)' }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const r = payload[0].payload as StorageRow;
            return (
              <TipBox
                title={`${r.name} · ${formatBytes(r.total)} in ${formatInt(r.tables)} tables`}
                rows={STORAGE.order.map((k) => ({ color: STORAGE.color[k], name: STORAGE.label[k], value: formatBytes(r[k]) }))}
              />
            );
          }}
        />
        <Legend formatter={(v) => STORAGE.label[String(v)] ?? v} wrapperStyle={{ fontSize: 11 }} />
        {STORAGE.order.map((k) => (
          <Bar
            key={k}
            dataKey={k}
            stackId="s"
            fill={STORAGE.color[k]}
            isAnimationActive={false}
            className={onClick ? 'bar-click' : undefined}
            onClick={onClick ? (d: any) => onClick(String(d?.payload?.name ?? d?.name ?? '')) : undefined}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function StoragePanel({ filters, drill, setDrill }: { filters: Filters; drill: string | null; setDrill: (v: string | null) => void }) {
  const byDb = useSQLQuery(Q.storageByDatabase(filters));
  const schemaFilters: Filters = { ...filters, database: drill ?? filters.database };
  const bySchema = useSQLQuery(Q.storageBySchema(schemaFilters));
  const dbRows = toRows(byDb.data);
  const schemaRows = toRows(bySchema.data);
  const dbHeight = Math.max(200, 28 * Math.min(dbRows.length, 20) + 60);
  const schemaHeight = Math.max(200, 24 * Math.min(schemaRows.length, 18) + 60);
  const scope = drill ?? filters.database;

  return (
    <Panel
      title="1 · Storage footprint"
      sub="Active vs time-travel vs fail-safe bytes per database (user objects unless the system-objects toggle is on). Fail-safe is retained by Snowflake and disappears on migration; it is stacked separately so it is never folded into the migrated total. Click a bar to drill into schemas; click it again to clear."
    >
      <Row>
        <div>
          <h3>by database</h3>
          {byDb.isError ? <ErrorNote error={byDb.error} /> : byDb.isLoading && dbRows.length === 0 ? <Skeleton height={dbHeight} /> : dbRows.length === 0 ? (
            <p style={{ color: MUTED, fontSize: 12 }}>No storage rows for this selection.</p>
          ) : (
            <StorageBars rows={dbRows} height={dbHeight} labelWidth={170} onClick={(name) => setDrill(drill === name ? null : name)} />
          )}
        </div>
        <div>
          <h3>
            by schema (top 18) · <span style={{ color: PRIMARY, textTransform: 'none', letterSpacing: 0 }}>{scope ? `drilled into ${scope}` : 'all databases'}</span>
          </h3>
          {bySchema.isError ? <ErrorNote error={bySchema.error} /> : bySchema.isLoading && schemaRows.length === 0 ? <Skeleton height={schemaHeight} /> : (
            <StorageBars rows={schemaRows} height={schemaHeight} labelWidth={230} />
          )}
        </div>
      </Row>
    </Panel>
  );
}
