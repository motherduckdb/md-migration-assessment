// 5. Migration-risk panel. Left: Snowflake-specific SQL constructs as a share of
// the queries the server-side heuristic scanned (query text was never
// collected; these are frequency signals). Right: the feature inventory with
// the collector's four observation states kept distinct:
//   observed       → solid bar proportional to count (symlog), "≥" when lower_bound
//   observed_zero  → dashed outline with a zero tick, labelled "0 observed"
//   unknown        → full-width hatched bar, reason on hover, no number
//   not_requested  → flat grey bar labelled "not requested": the collection
//                    profile did not include the source extract; no number
import { useSQLQuery } from '@motherduck/react-sql-query';
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { N, S, formatCount, formatInt, formatPct } from '../format';
import { MUTED } from '../palette';
import * as Q from '../queries';
import { ErrorNote, Panel, Row, Skeleton, TipBox } from '../ui';

const REASON: Record<string, string> = {
  not_visible: 'not visible to the collecting role',
  probe_failed: 'the probe query failed',
  extract_failed: 'the extractor failed',
};

type Construct = { construct: string; matched: number; scanned: number; rate: number; note: string };

function DialectConstructs() {
  const query = useSQLQuery(Q.dialectConstructs());
  const rows: Construct[] = (Array.isArray(query.data) ? query.data : []).map((r) => ({
    construct: S(r.construct).replaceAll('_', ' '),
    matched: N(r.n_queries_matched),
    scanned: N(r.n_queries_scanned),
    rate: N(r.match_rate),
    note: S(r.note),
  }));
  if (query.isError) return <ErrorNote error={query.error} />;
  if (query.isLoading && rows.length === 0) return <Skeleton height={260} />;
  if (rows.length === 0) return <p style={{ color: MUTED, fontSize: 12 }}>No dialect-construct rows (the query-history heuristic did not run).</p>;
  const scanned = rows[0]?.scanned ?? 0;
  return (
    <>
      <p style={{ color: MUTED, fontSize: 12, margin: '0 0 4px' }}>share of {formatInt(scanned)} scanned queries matching each construct</p>
      <ResponsiveContainer width="100%" height={Math.max(200, 26 * rows.length + 50)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 90, bottom: 4, left: 4 }} barCategoryGap={5}>
          <CartesianGrid strokeDasharray="3 3" stroke="#eee" horizontal={false} />
          <XAxis type="number" dataKey="rate" tickFormatter={(v) => formatPct(v, 1)} fontSize={11} domain={[0, (max: number) => Math.max(max * 1.15, 0.0001)]} />
          <YAxis type="category" dataKey="construct" width={150} fontSize={11} interval={0} />
          <Tooltip
            cursor={{ fill: 'rgba(7,119,179,.06)' }}
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const r = payload[0].payload as Construct;
              return <TipBox title={r.construct} rows={[{ name: 'matched', value: formatInt(r.matched) }, { name: 'scanned', value: formatInt(r.scanned) }, { name: 'share', value: formatPct(r.rate) }, ...(r.note ? [{ name: 'note', value: r.note }] : [])]} />;
            }}
          />
          <Bar dataKey="rate" isAnimationActive={false} minPointSize={2}>
            {rows.map((r) => (
              <Cell key={r.construct} fill={r.matched === 0 ? '#9aa0a6' : '#d1495b'} />
            ))}
            <LabelList dataKey="matched" position="right" fontSize={10} fill="#333" formatter={(v: unknown) => `${formatInt(v)} matched`} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </>
  );
}

type Feature = {
  category: string;
  feature: string;
  status: string;
  count: number | null;
  lower_bound: boolean;
  unknown_reason: string | null;
  sample_objects: string;
  source_extractor: string;
  note: string;
};

function FeatureRow({ f, scale }: { f: Feature; scale: (v: number) => number }) {
  const name = <span className="feat-name">{f.feature.replaceAll('_', ' ')}</span>;
  if (f.status === 'unknown') {
    const reason = f.unknown_reason ?? 'unknown';
    const title = `UNKNOWN — ${REASON[reason] ?? reason}` + (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') + (f.note ? `\n${f.note}` : '') + '\nNot measured. This is not a zero.';
    return (
      <div className="feat-row" title={title}>
        {name}
        <div className="feat-bar">
          <div className="feat-fill feat-unknown" style={{ width: '100%' }}>
            <span className="feat-unknown-label">unknown · {reason.replaceAll('_', ' ')}</span>
          </div>
        </div>
        <span className="feat-value" style={{ color: '#6d6d78', fontWeight: 700 }}>?</span>
      </div>
    );
  }
  if (f.status === 'not_requested') {
    const title = 'NOT REQUESTED — the collection profile did not include the source extract.' +
      (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') + (f.note ? `\n${f.note}` : '') +
      '\nNot measured. This is not a zero; re-collect with --profile standard to measure it.';
    return (
      <div className="feat-row" title={title}>
        {name}
        <div className="feat-bar">
          <div className="feat-fill feat-nr" style={{ width: '100%' }}>
            <span className="feat-nr-label">not requested · profile</span>
          </div>
        </div>
        <span className="feat-value" style={{ color: MUTED, fontStyle: 'italic' }}>not requested</span>
      </div>
    );
  }
  if (f.status === 'observed_zero') {
    const title = 'OBSERVED ZERO — the extractor ran and found none.' + (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') + (f.note ? `\n${f.note}` : '');
    return (
      <div className="feat-row" title={title}>
        {name}
        <div className="feat-bar">
          <div className="feat-fill feat-zero">
            <span className="feat-zero-tick" />
          </div>
        </div>
        <span className="feat-value" style={{ color: MUTED }}>0 observed</span>
      </div>
    );
  }
  const w = Math.max(1.5, 100 * scale(f.count ?? 0));
  const title =
    `OBSERVED — ${formatCount(f.count, f.lower_bound)}` +
    (f.lower_bound ? '\nlower bound: the collecting role may not see everything' : '') +
    (f.sample_objects ? `\nsamples: ${f.sample_objects}` : '') +
    (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') +
    (f.note ? `\n${f.note}` : '');
  return (
    <div className="feat-row" title={title}>
      {name}
      <div className="feat-bar">
        <div className="feat-fill feat-observed" style={{ width: `${w}%` }} />
      </div>
      <span className={`feat-value${f.lower_bound ? ' feat-lb' : ''}`}>{formatCount(f.count, f.lower_bound)}</span>
    </div>
  );
}

function FeatureInventory() {
  const query = useSQLQuery(Q.featureInventory());
  const feats: Feature[] = (Array.isArray(query.data) ? query.data : []).map((r) => ({
    category: S(r.category),
    feature: S(r.feature),
    status: S(r.observation_status),
    count: r.count == null ? null : N(r.count),
    lower_bound: Boolean(r.lower_bound),
    unknown_reason: r.unknown_reason == null ? null : S(r.unknown_reason),
    sample_objects: S(r.sample_objects),
    source_extractor: S(r.source_extractor),
    note: S(r.note),
  }));
  if (query.isError) return <ErrorNote error={query.error} />;
  if (query.isLoading && feats.length === 0) return <Skeleton height={400} />;
  const max = Math.max(1, ...feats.map((f) => f.count ?? 0));
  const scale = (v: number) => Math.log1p(v) / Math.log1p(max);
  const byCat = new Map<string, Feature[]>();
  for (const f of feats) byCat.set(f.category, [...(byCat.get(f.category) ?? []), f]);

  return (
    <div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 14, fontSize: 12, color: MUTED, margin: '4px 0 8px' }}>
        <span><i style={{ display: 'inline-block', width: 14, height: 10, background: '#3b6ea5', borderRadius: 2, verticalAlign: -1 }} /> observed</span>
        <span><i style={{ display: 'inline-block', width: 14, height: 10, border: '1.5px solid #9aa0a6', borderRadius: 2, verticalAlign: -1, boxSizing: 'border-box' }} /> observed zero</span>
        <span><i style={{ display: 'inline-block', width: 14, height: 10, background: 'repeating-linear-gradient(45deg, #e9e9ee 0 3px, #8a8a96 3px 5px)', borderRadius: 2, verticalAlign: -1 }} /> unknown (hover for reason)</span>
        {feats.some((f) => f.status === 'not_requested') ? (
          <span><i style={{ display: 'inline-block', width: 14, height: 10, background: '#e3e5ea', border: '1px solid #c9c9d1', borderRadius: 2, verticalAlign: -1, boxSizing: 'border-box' }} /> not requested by the profile</span>
        ) : null}
        <span><b>≥</b> lower bound</span>
      </div>
      {[...byCat.entries()].map(([cat, list]) => {
        const unknown = list.filter((f) => f.status === 'unknown').length;
        const observed = list.filter((f) => f.status === 'observed').length;
        const zero = list.filter((f) => f.status === 'observed_zero').length;
        const notRequested = list.filter((f) => f.status === 'not_requested').length;
        return (
          <section key={cat} style={{ marginBottom: 10 }}>
            <h4 style={{ display: 'flex', justifyContent: 'space-between', margin: '6px 0 3px', fontSize: 12, textTransform: 'uppercase', letterSpacing: '.03em', borderBottom: '1px solid #e5e5e5', paddingBottom: 2 }}>
              <span>{cat.replaceAll('_', ' ')}</span>
              <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: MUTED }}>
                {observed} observed · {zero} zero · {unknown} unknown{notRequested ? ` · ${notRequested} not requested` : ''}
              </span>
            </h4>
            {list.map((f) => (
              <FeatureRow key={f.feature} f={f} scale={scale} />
            ))}
          </section>
        );
      })}
    </div>
  );
}

export function RiskPanel() {
  return (
    <Panel
      title="5 · Migration-risk panel"
      sub="Left: Snowflake-specific SQL constructs as a share of the queries the server-side heuristic scanned (query text was never collected; these are frequency signals). Right: feature inventory by category with the collector's observed / observed-zero / unknown states preserved."
    >
      <Row>
        <div>
          <h3>dialect constructs</h3>
          <DialectConstructs />
        </div>
        <div>
          <h3>feature inventory</h3>
          <FeatureInventory />
        </div>
      </Row>
    </Panel>
  );
}
