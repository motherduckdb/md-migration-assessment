// 2. Spend over time: daily credits per warehouse (Snowflake credits, never
// dollars: no rate is recorded). The brush on this chart sets the date range
// the workload and concurrency panels use.
import { useEffect, useMemo, useRef } from 'react';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Area, AreaChart, Brush, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatCredits, formatInt, pivot } from '../format';
import { MUTED } from '../palette';
import * as Q from '../queries';
import type { Filters } from '../queries';
import { ErrorNote, Panel, Skeleton, TipBox } from '../ui';

export type DateRange = { start: string; end: string } | null;

export function SpendPanel({
  filters,
  warehouses,
  color,
  range,
  setRange,
}: {
  filters: Filters;
  warehouses: string[];
  color: (name: string) => string;
  range: DateRange;
  setRange: (r: DateRange) => void;
}) {
  const query = useSQLQuery(Q.spendDaily(filters));
  const rows = useMemo(() => {
    const long = (Array.isArray(query.data) ? query.data : []).map((r) => ({ day: S(r.day), warehouse_name: S(r.warehouse_name), credits: N(r.credits) }));
    return pivot(long, 'day', 'warehouse_name', 'credits').sort((a, b) => String(a.day).localeCompare(String(b.day)));
  }, [query.data]);
  const visible = filters.warehouses.length ? warehouses.filter((w) => filters.warehouses.includes(w)) : warehouses;
  const total = rows.reduce((acc, r) => acc + visible.reduce((s, w) => s + N(r[w]), 0), 0);

  // Brush → date range, debounced so dragging does not fire a query per pixel.
  const timer = useRef<number | null>(null);
  const onBrush = (e: { startIndex?: number; endIndex?: number }) => {
    if (e.startIndex == null || e.endIndex == null || rows.length === 0) return;
    const start = String(rows[Math.max(0, e.startIndex)].day);
    const end = String(rows[Math.min(rows.length - 1, e.endIndex)].day);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const whole = e.startIndex === 0 && e.endIndex === rows.length - 1;
      setRange(whole ? null : { start, end });
    }, 250);
  };
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const startIndex = range ? Math.max(0, rows.findIndex((r) => String(r.day) >= range.start)) : 0;
  const endIndexRaw = range ? rows.findIndex((r) => String(r.day) > range.end) : -1;
  const endIndex = endIndexRaw === -1 ? Math.max(0, rows.length - 1) : Math.max(0, endIndexRaw - 1);

  return (
    <Panel
      title="2 · Spend over time"
      sub={
        <>
          Daily Snowflake credits per warehouse (compute + cloud services) over the history window. Drag the brush to set the date range for the workload and concurrency panels; the warehouse control filters every panel.
          {rows.length ? <> Window total for the shown warehouses: <strong>{formatCredits(total)}</strong> credits.</> : null}
        </>
      }
    >
      {query.isError ? <ErrorNote error={query.error} /> : query.isLoading && rows.length === 0 ? <Skeleton height={300} /> : rows.length === 0 ? (
        <p style={{ color: MUTED, fontSize: 12 }}>No spend rows for this selection.</p>
      ) : (
        <ResponsiveContainer width="100%" height={320}>
          <AreaChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
            <XAxis dataKey="day" fontSize={11} minTickGap={40} tickFormatter={(d) => String(d).slice(0, 7)} />
            <YAxis fontSize={11} tickFormatter={(v) => (Number(v) >= 10 ? formatInt(v) : formatCredits(v))} width={56} label={{ value: 'credits / day', angle: -90, position: 'insideLeft', fontSize: 11, fill: MUTED }} />
            <Tooltip
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const items = [...payload].filter((p) => N(p.value) > 0).sort((a, b) => N(b.value) - N(a.value));
                const sum = items.reduce((s, p) => s + N(p.value), 0);
                return (
                  <TipBox
                    title={`${label} · ${formatCredits(sum)} credits`}
                    rows={items.map((p) => ({ color: String(p.color), name: String(p.name), value: formatCredits(p.value) }))}
                  />
                );
              }}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {visible.map((w) => (
              <Area key={w} type="linear" dataKey={w} stackId="1" stroke={color(w)} fill={color(w)} fillOpacity={0.85} isAnimationActive={false} />
            ))}
            <Brush
              dataKey="day"
              height={26}
              stroke="#0777b3"
              travellerWidth={8}
              startIndex={startIndex}
              endIndex={endIndex}
              onChange={onBrush}
              tickFormatter={(d) => String(d).slice(0, 7)}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Panel>
  );
}
