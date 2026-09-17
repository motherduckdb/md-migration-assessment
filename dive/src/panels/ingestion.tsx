// 6. Ingestion and client tools. Left: bytes loaded per database in the window
// by load method (follows the database selection). Right: client-tool
// fingerprints from session metadata, ranked by query events.
import { useMemo } from 'react';
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatBytes, formatInt, formatMs, pivot } from '../format';
import { CONFIDENCE, LOAD_METHOD, MUTED } from '../palette';
import * as Q from '../queries';
import type { Filters } from '../queries';
import { ErrorNote, Panel, Row, Skeleton, TipBox } from '../ui';

function Ingestion({ filters }: { filters: Filters }) {
  const query = useSQLQuery(Q.ingestion(filters));
  const { rows, methods, detail } = useMemo(() => {
    const long = (Array.isArray(query.data) ? query.data : []).map((r) => ({
      name: S(r.name),
      load_method: S(r.load_method),
      bytes_loaded: N(r.bytes_loaded),
      files: N(r.files),
      rows_loaded: N(r.rows_loaded),
      tables: N(r.tables),
    }));
    const methods = [...new Set(long.map((r) => r.load_method))].sort();
    const rows = pivot(long, 'name', 'load_method', 'bytes_loaded').sort(
      (a, b) => methods.reduce((s, m) => s + N(b[m]), 0) - methods.reduce((s, m) => s + N(a[m]), 0),
    );
    const detail = new Map<string, { files: number; rows_loaded: number; tables: number }>();
    for (const r of long) {
      const d = detail.get(r.name) ?? { files: 0, rows_loaded: 0, tables: 0 };
      detail.set(r.name, { files: d.files + r.files, rows_loaded: d.rows_loaded + r.rows_loaded, tables: d.tables + r.tables });
    }
    return { rows, methods, detail };
  }, [query.data]);
  if (query.isError) return <ErrorNote error={query.error} />;
  if (query.isLoading && rows.length === 0) return <Skeleton height={260} />;
  if (rows.length === 0) return <p style={{ color: MUTED, fontSize: 12 }}>No ingestion rows for this selection.</p>;
  return (
    <ResponsiveContainer width="100%" height={Math.max(200, 30 * Math.min(rows.length, 12) + 60)}>
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 4 }} barCategoryGap={4}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eee" horizontal={false} />
        <XAxis type="number" tickFormatter={(v) => formatBytes(v, 0)} fontSize={11} tickCount={5} />
        <YAxis type="category" dataKey="name" width={150} fontSize={11} interval={0} />
        <Tooltip
          cursor={{ fill: 'rgba(7,119,179,.06)' }}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            const d = detail.get(String(label));
            return (
              <TipBox
                title={String(label)}
                rows={[
                  ...payload.filter((p) => N(p.value) > 0).map((p) => ({ color: String(p.color), name: String(p.name), value: formatBytes(p.value) })),
                  ...(d ? [{ name: 'tables', value: formatInt(d.tables) }, { name: 'files', value: formatInt(d.files) }, { name: 'rows loaded', value: formatInt(d.rows_loaded) }] : []),
                ]}
              />
            );
          }}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {methods.map((m, i) => (
          <Bar key={m} dataKey={m} stackId="m" fill={LOAD_METHOD[m] ?? ['#3b6ea5', '#edae49', '#9aa0a6'][i % 3]} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

type Tool = { tool: string; detection_method: string; confidence: string; n_events: number; n_distinct_users: number; sum_elapsed_ms: number };

function Tools() {
  const query = useSQLQuery(Q.tools());
  const rows: Tool[] = (Array.isArray(query.data) ? query.data : []).map((r) => ({
    tool: S(r.tool),
    detection_method: S(r.detection_method),
    confidence: S(r.confidence),
    n_events: N(r.n_events),
    n_distinct_users: N(r.n_distinct_users),
    sum_elapsed_ms: N(r.sum_elapsed_ms),
  }));
  if (query.isError) return <ErrorNote error={query.error} />;
  if (query.isLoading && rows.length === 0) return <Skeleton height={260} />;
  if (rows.length === 0) return <p style={{ color: MUTED, fontSize: 12 }}>No tool fingerprints (session metadata was not collected).</p>;
  return (
    <>
      <ResponsiveContainer width="100%" height={Math.max(200, 24 * rows.length + 50)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 4 }} barCategoryGap={5}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" horizontal={false} />
          <XAxis type="number" dataKey="n_events" tickFormatter={(v) => formatInt(v)} fontSize={11} />
          <YAxis type="category" dataKey="tool" width={150} fontSize={11} interval={0} />
          <Tooltip
            cursor={{ fill: 'rgba(7,119,179,.06)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const t = payload[0].payload as Tool;
              return (
                <TipBox
                  title={t.tool}
                  rows={[
                    { name: 'query events', value: formatInt(t.n_events) },
                    { name: 'distinct users', value: formatInt(t.n_distinct_users) },
                    { name: 'elapsed (server-side)', value: formatMs(t.sum_elapsed_ms) },
                    { name: 'detection', value: `${t.detection_method} · ${t.confidence} confidence` },
                  ]}
                />
              );
            }}
          />
          <Bar dataKey="n_events" isAnimationActive={false} minPointSize={2}>
            {rows.map((t) => (
              <Cell key={t.tool} fill={CONFIDENCE[t.confidence] ?? '#9aa0a6'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div style={{ display: 'flex', gap: 12, fontSize: 11, color: MUTED }}>
        {Object.entries(CONFIDENCE).map(([k, c]) => (
          <span key={k}>
            <i style={{ display: 'inline-block', width: 10, height: 10, background: c, borderRadius: 2, verticalAlign: -1, marginRight: 4 }} />
            {k} confidence
          </span>
        ))}
      </div>
    </>
  );
}

export function IngestionPanel({ filters }: { filters: Filters }) {
  return (
    <Panel
      title="6 · Ingestion and client tools"
      sub="Left: bytes loaded per database in the window by load method (follows the database selection). Right: client-tool fingerprints from session metadata, ranked by query events; colour is detection confidence."
    >
      <Row>
        <div>
          <h3>ingestion inventory</h3>
          <Ingestion filters={filters} />
        </div>
        <div>
          <h3>tool fingerprints (top 14)</h3>
          <Tools />
        </div>
      </Row>
    </Panel>
  );
}
