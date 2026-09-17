// 3. Workload profile: each dot is a (warehouse, query type) pair over the
// selected window, bytes scanned vs server-side elapsed time, sized by query
// count, one small multiple per query class so transformations and file
// operations never blur together. Both axes are symlog (zero stays on-axis).
import { useMemo } from 'react';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { CartesianGrid, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';
import { N, S, formatBytes, formatInt, formatMs, symlog } from '../format';
import { MUTED, QUERY_CLASSES } from '../palette';
import * as Q from '../queries';
import type { Filters } from '../queries';
import { ErrorNote, Panel, Skeleton, TipBox } from '../ui';

type Point = {
  warehouse_name: string;
  query_type: string;
  query_class: string;
  n_queries: number;
  elapsed_ms: number;
  bytes_scanned: number;
  n_spilled_remote: number;
  queued_ms: number;
  x: number;
  y: number;
  fill: string;
};

const BYTE_TICKS = [0, 2 ** 20, 2 ** 30, 2 ** 40, 2 ** 50];
const MS_TICKS = [0, 1e3, 6e4, 3.6e6, 8.64e7, 8.64e8, 8.64e9, 8.64e10];

export function WorkloadPanel({ filters, color }: { filters: Filters; color: (name: string) => string }) {
  const query = useSQLQuery(Q.workloadPoints(filters));
  const points: Point[] = useMemo(
    () =>
      (Array.isArray(query.data) ? query.data : []).map((r) => {
        const bytes = N(r.bytes_scanned);
        const ms = N(r.elapsed_ms);
        return {
          warehouse_name: S(r.warehouse_name),
          query_type: S(r.query_type),
          query_class: S(r.query_class),
          n_queries: N(r.n_queries),
          elapsed_ms: ms,
          bytes_scanned: bytes,
          n_spilled_remote: N(r.n_spilled_remote),
          queued_ms: N(r.queued_ms),
          x: symlog(bytes),
          y: symlog(ms),
          fill: color(S(r.warehouse_name)),
        };
      }),
    [query.data, color],
  );

  // Shared axes across the small multiples, from the unfiltered-by-class extent.
  const xmax = symlog(Math.max(2 ** 20, ...points.map((p) => p.bytes_scanned)) * 1.2);
  const ymax = symlog(Math.max(1e3, ...points.map((p) => p.elapsed_ms)) * 1.2);
  const zmax = Math.max(1, ...points.map((p) => p.n_queries));
  const xTicks = BYTE_TICKS.map(symlog).filter((t) => t <= xmax);
  const yTicks = MS_TICKS.map(symlog).filter((t) => t <= ymax);
  const classes = QUERY_CLASSES.filter((c) => points.some((p) => p.query_class === c));

  return (
    <Panel
      title="3 · Workload profile"
      sub="Each dot is a (warehouse, query type) pair over the selected window: bytes scanned vs server-side elapsed time, sized by query count, one chart per query class so transformations (CTAS, MERGE, …) and file operations (PUT / LIST / REMOVE) never blur together. Both axes are symlog so zero-byte file operations stay visible at the left edge."
    >
      {query.isError ? <ErrorNote error={query.error} /> : query.isLoading && points.length === 0 ? <Skeleton height={300} /> : points.length === 0 ? (
        <p style={{ color: MUTED, fontSize: 12 }}>No workload rows for this selection.</p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(260px, 1fr))`, gap: 8 }}>
          {classes.map((cls) => {
            const data = points.filter((p) => p.query_class === cls);
            return (
              <div key={cls}>
                <h3 style={{ margin: '0 0 2px' }}>{cls} · {formatInt(data.reduce((s, p) => s + p.n_queries, 0))} queries</h3>
                <ResponsiveContainer width="100%" height={260}>
                  <ScatterChart margin={{ top: 12, right: 16, bottom: 8, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
                    <XAxis type="number" dataKey="x" domain={[0, xmax]} ticks={xTicks} tickFormatter={(t) => (t === 0 ? '0' : formatBytes(BYTE_TICKS[xTicks.indexOf(t)] ?? 0, 0))} fontSize={10} label={{ value: 'bytes scanned', position: 'insideBottomRight', offset: -4, fontSize: 10, fill: MUTED }} />
                    <YAxis type="number" dataKey="y" domain={[0, ymax]} ticks={yTicks} tickFormatter={(t) => (t === 0 ? '0' : formatMs(MS_TICKS[yTicks.indexOf(t)] ?? 0))} fontSize={10} width={52} />
                    <ZAxis type="number" dataKey="n_queries" range={[16, 900]} domain={[0, zmax]} />
                    <Tooltip
                      cursor={{ strokeDasharray: '3 3' }}
                      content={({ active, payload }) => {
                        if (!active || !payload?.length) return null;
                        const p = payload[0].payload as Point;
                        return (
                          <TipBox
                            title={`${p.warehouse_name} · ${p.query_type}`}
                            rows={[
                              { name: 'queries', value: formatInt(p.n_queries) },
                              { name: 'bytes scanned', value: formatBytes(p.bytes_scanned) },
                              { name: 'elapsed (server-side)', value: formatMs(p.elapsed_ms) },
                              { name: 'queued overload', value: formatMs(p.queued_ms) },
                              { name: 'spilled to remote', value: formatInt(p.n_spilled_remote) },
                            ]}
                          />
                        );
                      }}
                    />
                    <Scatter data={data} fillOpacity={0.75} stroke="#fff" strokeWidth={0.5} isAnimationActive={false} />
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}
