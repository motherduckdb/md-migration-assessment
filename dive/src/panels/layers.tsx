// 2b. Spend by layer (Databricks only): DBUs and list cost per layer
// (ingestion, transformation, BI serving, interactive, ML/AI, platform) over
// the selected dates. Billed rows come straight from the product each DBU was
// billed to; estimated rows are SQL warehouse DBUs split between BI serving and
// transformation by the warehouse's share of elapsed time in write statements.
// Renders nothing when report.spend_by_layer is absent (Snowflake collections).
import { useMemo } from 'react';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatCredits, formatInt, formatPct } from '../format';
import { MUTED } from '../palette';
import * as Q from '../queries';
import type { Filters } from '../queries';
import { ErrorNote, Panel, Skeleton, TipBox } from '../ui';

const LAYERS: Array<{ key: string; label: string }> = [
  { key: 'ingestion', label: 'Ingestion' },
  { key: 'transformation', label: 'Transformation' },
  { key: 'bi_serving', label: 'BI serving' },
  { key: 'interactive', label: 'Interactive / exploration' },
  { key: 'ml_ai', label: 'ML / AI' },
  { key: 'platform', label: 'Platform overhead' },
];
const BILLED = '#0777b3';
const ESTIMATED = '#9cc7e0';

type LayerRow = { layer: string; billed: number; estimated: number; dbus: number; cost: number; hasCost: boolean; products: string };

const money = (v: number) => `$${Math.round(v).toLocaleString('en-US')}`;

function Layers({ filters }: { filters: Filters }) {
  const query = useSQLQuery(Q.spendByLayer(filters));
  const { rows, total, totalCost, statuses } = useMemo(() => {
    const data = Array.isArray(query.data) ? query.data : [];
    const by = new Map<string, LayerRow>();
    const statuses = new Set<string>();
    for (const r of data) {
      const key = S(r.layer);
      const label = LAYERS.find((l) => l.key === key)?.label ?? key;
      const row = by.get(key) ?? { layer: label, billed: 0, estimated: 0, dbus: 0, cost: 0, hasCost: false, products: '' };
      const dbus = N(r.dbus);
      if (S(r.method) === 'estimated') row.estimated += dbus;
      else row.billed += dbus;
      row.dbus += dbus;
      row.cost += N(r.list_cost);
      row.hasCost ||= r.list_cost != null;
      row.products = [...new Set([...row.products.split(', '), ...S(r.products).split(', ')].filter(Boolean))].sort().join(', ');
      by.set(key, row);
      for (const s of S(r.extract_status).split(', ').filter(Boolean)) statuses.add(s);
    }
    const order = (k: string) => { const i = LAYERS.findIndex((l) => l.key === k); return i === -1 ? 99 : i; };
    const rows = [...by.entries()].sort((a, b) => order(a[0]) - order(b[0])).map(([, v]) => v);
    return {
      rows,
      total: rows.reduce((s, r) => s + r.dbus, 0),
      totalCost: rows.reduce((s, r) => s + r.cost, 0),
      statuses: [...statuses],
    };
  }, [query.data]);

  if (query.isError) return <ErrorNote error={query.error} />;
  if (query.isLoading && rows.length === 0) return <Skeleton height={240} />;
  if (rows.length === 0) return <p style={{ color: MUTED, fontSize: 12 }}>No billed DBUs for this selection.</p>;
  const anyEstimated = rows.some((r) => r.estimated > 0);
  const incomplete = statuses.filter((s) => s !== 'complete');

  return (
    <>
      {incomplete.length ? (
        <p style={{ color: MUTED, fontSize: 12, margin: '0 0 6px' }}>
          Billing extract status: <span className="chip chip-unknown">{incomplete.join(', ')}</span>, so these totals are a floor.
        </p>
      ) : null}
      <ResponsiveContainer width="100%" height={Math.max(180, 34 * rows.length + 60)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }} barCategoryGap={6}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" horizontal={false} />
          <XAxis type="number" fontSize={11} tickFormatter={(v) => formatInt(v)} label={{ value: 'DBUs', position: 'insideBottomRight', offset: -2, fontSize: 11, fill: MUTED }} />
          <YAxis type="category" dataKey="layer" width={170} fontSize={11} interval={0} />
          <Tooltip
            cursor={{ fill: 'rgba(7,119,179,.06)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const r = payload[0].payload as LayerRow;
              return (
                <TipBox
                  title={`${r.layer} · ${formatCredits(r.dbus)} DBUs`}
                  rows={[
                    { color: BILLED, name: 'billed (by product)', value: formatCredits(r.billed) },
                    ...(r.estimated > 0 ? [{ color: ESTIMATED, name: 'estimated (SQL warehouse split)', value: formatCredits(r.estimated) }] : []),
                    { name: 'list cost', value: r.hasCost ? money(r.cost) : 'no list price' },
                    { name: 'products', value: r.products },
                  ]}
                />
              );
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="billed" name="billed (by product)" stackId="1" fill={BILLED} isAnimationActive={false} />
          {anyEstimated ? <Bar dataKey="estimated" name="estimated (SQL warehouse split)" stackId="1" fill={ESTIMATED} isAnimationActive={false} /> : null}
        </BarChart>
      </ResponsiveContainer>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, marginTop: 8 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: MUTED }}>
            <th>Layer</th>
            <th style={{ textAlign: 'right' }}>DBUs</th>
            <th style={{ textAlign: 'right' }}>Share</th>
            <th style={{ textAlign: 'right' }}>List cost</th>
            <th style={{ textAlign: 'right' }}>Estimated DBUs</th>
            <th style={{ paddingLeft: 12 }}>Billing products</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.layer} style={{ borderTop: '1px solid #eee' }}>
              <td>{r.layer}</td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatCredits(r.dbus)}</td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatPct(total ? r.dbus / total : null, 1)}</td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.hasCost ? money(r.cost) : '—'}</td>
              <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.estimated > 0 ? formatCredits(r.estimated) : '—'}</td>
              <td style={{ paddingLeft: 12, color: MUTED }}>{r.products}</td>
            </tr>
          ))}
          <tr style={{ borderTop: '1px solid #ccc', fontWeight: 600 }}>
            <td>Total</td>
            <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{formatCredits(total)}</td>
            <td />
            <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(totalCost)}</td>
            <td />
            <td />
          </tr>
        </tbody>
      </table>
    </>
  );
}

export function SpendByLayerPanel({ filters }: { filters: Filters }) {
  const exists = useSQLQuery(Q.spendByLayerExists());
  const present = N((Array.isArray(exists.data) ? exists.data : [])[0]?.n) > 0;
  // Older Databricks collections (built before this table existed) show nothing rather than an error.
  if (exists.isError || (!exists.isLoading && !present)) return null;
  return (
    <Panel
      title="2b · Spend by layer"
      sub={
        <>
          DBUs and list cost per layer over the selected dates, from the product each DBU was billed to (Lakeflow pipelines count as ingestion; anything not in the map counts as platform).{' '}
          <span style={{ color: BILLED, fontWeight: 600 }}>Billed</span> is a billing fact;{' '}
          <span style={{ color: '#5b9cc4', fontWeight: 600 }}>estimated</span> is SQL warehouse DBUs split between BI serving and transformation by each warehouse's daily share of time in write statements. List price, never the negotiated rate. The warehouse control does not apply here.
        </>
      }
    >
      {exists.isLoading && !present ? <Skeleton height={240} /> : <Layers filters={filters} />}
    </Panel>
  );
}
