// 4. Concurrency and contention: peak (solid) vs average (dashed) concurrent
// queries per warehouse, with queued-overload time per day underneath on the
// same time axis, so contention sits next to the bursts that cause it.
import { useMemo } from 'react';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatInt, formatMs, pivot } from '../format';
import { MUTED } from '../palette';
import * as Q from '../queries';
import type { Filters, Grain } from '../queries';
import { ErrorNote, Panel, Skeleton, TipBox } from '../ui';

export function ConcurrencyPanel({
  filters,
  warehouses,
  color,
  windowDays,
}: {
  filters: Filters;
  warehouses: string[];
  color: (name: string) => string;
  windowDays: number;
}) {
  // hourly points for long windows are too many for an SVG chart: coarsen to days
  const grain: Grain = windowDays > 62 ? 'day' : 'hour';
  const conc = useSQLQuery(Q.concurrency(filters, grain));
  const queued = useSQLQuery(Q.queuedOverload(filters));
  const visible = filters.warehouses.length ? warehouses.filter((w) => filters.warehouses.includes(w)) : warehouses;

  const concRows = useMemo(() => {
    const long = (Array.isArray(conc.data) ? conc.data : []).map((r) => ({ t: S(r.t), warehouse_name: S(r.warehouse_name), peak: N(r.peak), avg_c: N(r.avg_c) }));
    const peak = pivot(long, 't', 'warehouse_name', 'peak');
    const avg = pivot(long.map((r) => ({ ...r, warehouse_name: `${r.warehouse_name} (avg)` })), 't', 'warehouse_name', 'avg_c');
    const byT = new Map<string, Record<string, number | string>>();
    for (const r of [...peak, ...avg]) byT.set(String(r.t), { ...(byT.get(String(r.t)) ?? {}), ...r });
    return [...byT.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
  }, [conc.data]);

  const queuedRows = useMemo(() => {
    const long = (Array.isArray(queued.data) ? queued.data : []).map((r) => ({ day: S(r.day), warehouse_name: S(r.warehouse_name), queued_ms: N(r.queued_ms) }));
    return pivot(long, 'day', 'warehouse_name', 'queued_ms').sort((a, b) => String(a.day).localeCompare(String(b.day)));
  }, [queued.data]);

  const tickFmt = (t: unknown) => (grain === 'day' ? String(t).slice(0, 7) : String(t).slice(5, 13));

  return (
    <Panel
      title="4 · Concurrency and contention"
      sub={`Per-${grain} peak (solid) against average (dashed) concurrent queries per warehouse, with total queued-overload time per day from the workload rollup underneath, aligned on the same time axis. ${grain === 'day' ? 'Hourly values are coarsened to daily maxima for windows longer than two months; brush a shorter range on the spend chart to see hours.' : ''}`}
    >
      {conc.isError ? <ErrorNote error={conc.error} /> : conc.isLoading && concRows.length === 0 ? <Skeleton height={260} /> : concRows.length === 0 ? (
        <p style={{ color: MUTED, fontSize: 12 }}>No concurrency rows for this selection.</p>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={concRows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
            <XAxis dataKey="t" fontSize={11} minTickGap={40} tickFormatter={tickFmt} />
            <YAxis fontSize={11} width={44} allowDecimals={false} label={{ value: 'concurrent queries', angle: -90, position: 'insideLeft', fontSize: 11, fill: MUTED }} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const items = [...payload].filter((p) => N(p.value) > 0).sort((a, b) => N(b.value) - N(a.value));
                return <TipBox title={String(label)} rows={items.map((p) => ({ color: String(p.color), name: String(p.name), value: String(p.name).endsWith('(avg)') ? N(p.value).toFixed(1) : formatInt(p.value) }))} />;
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {visible.map((w) => (
              <Line key={`${w}-avg`} type="stepAfter" dataKey={`${w} (avg)`} stroke={color(w)} strokeOpacity={0.5} strokeWidth={1} strokeDasharray="2 2" dot={false} isAnimationActive={false} connectNulls legendType="none" />
            ))}
            {visible.map((w) => (
              <Line key={w} type="stepAfter" dataKey={w} stroke={color(w)} strokeWidth={1.3} dot={false} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
      <h3>queued (overload) time per day</h3>
      {queued.isError ? <ErrorNote error={queued.error} /> : queued.isLoading && queuedRows.length === 0 ? <Skeleton height={170} /> : queuedRows.length === 0 ? (
        <p style={{ color: MUTED, fontSize: 12 }}>No queued-overload time in this selection.</p>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={queuedRows} margin={{ top: 4, right: 16, bottom: 0, left: 0 }} barCategoryGap={0}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
            <XAxis dataKey="day" fontSize={11} minTickGap={40} tickFormatter={(d) => String(d).slice(0, 7)} />
            <YAxis fontSize={11} width={56} tickFormatter={(v) => formatMs(v)} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const items = [...payload].filter((p) => N(p.value) > 0).sort((a, b) => N(b.value) - N(a.value));
                return <TipBox title={String(label)} rows={items.map((p) => ({ color: String(p.color), name: String(p.name), value: formatMs(p.value) }))} />;
              }}
            />
            {visible.map((w) => (
              <Bar key={w} dataKey={w} stackId="q" fill={color(w)} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}
