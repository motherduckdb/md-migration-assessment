// Coverage strip: from meta.extract_runs, how many extractors completed,
// failed, were unavailable, were not requested by the collection profile, or
// completed against an Enterprise-only source on a non-Enterprise account
// (0 rows that cannot be distinguished from "unavailable"). Always visible so
// the dashboard cannot be read as complete when it isn't: every extractor
// lands in exactly one segment, so a lite collection shows how much of the
// assessment was deliberately not run.
import { useSQLQuery } from '@motherduck/react-sql-query';
import { N, S } from './format';
import { MUTED } from './palette';
import * as Q from './queries';
import { ErrorNote, Skeleton } from './ui';

type Run = {
  extractor: string;
  status: string;
  source_used: string | null;
  rows_written: number;
  min_edition: string | null;
  error_category: string | null;
  error_detail: string | null;
};

// Enterprise-gated sources that returned 0 rows split two ways:
//  - activity/history sources (ACCESS_HISTORY for table_read_heat): the view exists
//    but is never populated below Enterprise, so 0 rows is UNMEASURED, not zero.
//  - object catalogs for Enterprise-only features (masking / row-access policies,
//    tags): the feature cannot exist on this account, so 0 rows is a real zero.
const isActivitySource = (r: Run) => /(_heat|_history|_usage)$/.test(r.extractor) || r.extractor === 'table_read_heat';

const NOT_REQUESTED_BG = '#c9c9d1';

export function CoverageStrip() {
  const query = useSQLQuery(Q.extractRuns());
  if (query.isError) return <ErrorNote error={query.error} />;
  const runs: Run[] = (Array.isArray(query.data) ? query.data : []).map((r) => ({
    extractor: S(r.extractor),
    status: S(r.status),
    source_used: r.source_used == null ? null : S(r.source_used),
    rows_written: N(r.rows_written),
    min_edition: r.min_edition == null ? null : S(r.min_edition),
    error_category: r.error_category == null ? null : S(r.error_category),
    error_detail: r.error_detail == null ? null : S(r.error_detail),
  }));
  if (query.isLoading && runs.length === 0) return <Skeleton height={56} />;

  const notRequested = runs.filter((r) => r.status === 'not_requested');
  const failed = runs.filter((r) => r.status === 'failed');
  const unavailable = runs.filter((r) => r.status === 'unavailable' || r.status === 'partial' || r.status === 'interrupted');
  const gated = runs.filter((r) => r.status === 'complete' && r.rows_written === 0 && r.min_edition === 'ENTERPRISE');
  const editionLimited = gated.filter(isActivitySource);
  const gatedZero = gated.filter((r) => !isActivitySource(r));
  const complete = runs.filter((r) => r.status === 'complete' && !editionLimited.includes(r));
  const total = runs.length;
  const accounted = complete.length + editionLimited.length + unavailable.length + failed.length + notRequested.length;
  const other = runs.filter(
    (r) => !complete.includes(r) && !editionLimited.includes(r) && !unavailable.includes(r) && !failed.includes(r) && !notRequested.includes(r),
  );

  const seg = (bg: string, n: number, label: string, title: string, fg = '#fff') =>
    n === 0 ? null : (
      <span key={label} className="cov-seg" style={{ flex: n, background: bg, color: fg }} title={title}>
        {n} {label}
      </span>
    );

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 6, flexWrap: 'wrap' }}>
        <strong>Collection coverage</strong>
        <span style={{ color: MUTED }}>
          · {total} extractors
          {notRequested.length ? ` · ${notRequested.length} not requested by this collection's profile` : ''}
        </span>
        <span style={{ marginLeft: 'auto', color: MUTED, fontSize: 12 }}>Charts below only cover what the collector could measure.</span>
      </div>
      <div style={{ display: 'flex', height: 22, borderRadius: 4, overflow: 'hidden', gap: 2 }}>
        {seg('#3f8f5a', complete.length, 'complete', 'completed with evidence')}
        {seg('repeating-linear-gradient(45deg, #8a8a96 0 4px, #6d6d78 4px 8px)', editionLimited.length, 'edition-limited → unknown', editionLimited.map((r) => r.extractor).join(', '))}
        {seg('#b58b3a', unavailable.length, 'unavailable / partial', unavailable.map((r) => r.extractor).join(', '))}
        {seg('#c5443f', failed.length, 'failed', failed.map((r) => r.extractor).join(', '))}
        {seg(NOT_REQUESTED_BG, notRequested.length, 'not requested', `not part of this collection's profile: ${notRequested.map((r) => r.extractor).join(', ')}`, '#2b2b33')}
        {seg('#6d6d78', other.length, 'other', other.map((r) => `${r.extractor} (${r.status})`).join(', '))}
      </div>
      <details style={{ marginTop: 6, fontSize: 12, color: MUTED }}>
        <summary>extractor detail</summary>
        <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
          {failed.map((r) => (
            <li key={r.extractor}>
              <span className="chip chip-failed">failed</span> <code>{r.extractor}</code> — {r.error_detail ?? r.error_category ?? 'error'}
            </li>
          ))}
          {unavailable.map((r) => (
            <li key={r.extractor}>
              <span className="chip chip-zero">{r.status}</span> <code>{r.extractor}</code> — {r.error_detail ?? r.error_category ?? 'not readable by the collecting role or edition'}
            </li>
          ))}
          {editionLimited.map((r) => (
            <li key={r.extractor}>
              <span className="chip chip-unknown">edition-limited → unknown</span> <code>{r.extractor}</code> — completed with 0 rows from an Enterprise-only activity source ({r.source_used ?? 'account_usage'}); on this account that is unmeasured, not zero.
              {r.extractor === 'table_read_heat' ? ' ACCESS_HISTORY is never populated below Enterprise, so there is no table read-heat evidence; do not read this as "no reads".' : ''}
            </li>
          ))}
          {gatedZero.map((r) => (
            <li key={r.extractor}>
              <span className="chip chip-zero">observed zero</span> <code>{r.extractor}</code> — 0 rows; the feature itself requires Enterprise, so zero objects is expected and real on this account.
            </li>
          ))}
          {notRequested.length ? (
            <li>
              <span className="chip" style={{ background: NOT_REQUESTED_BG, color: '#2b2b33' }}>not requested</span> {notRequested.length} extractors were not part of this collection's profile, so their evidence is absent (not zero):{' '}
              {notRequested.map((r, i) => (
                <span key={r.extractor}>
                  {i ? ', ' : ''}
                  <code>{r.extractor}</code>
                </span>
              ))}
              . Re-collect with <code>--profile standard</code> to include them.
            </li>
          ) : null}
          {other.map((r) => (
            <li key={r.extractor}>
              <span className="chip chip-zero">{r.status}</span> <code>{r.extractor}</code>
            </li>
          ))}
          <li>
            <span className="chip chip-zero">not visible</span> Feature-inventory rows marked unknown / not_visible come from catalogs the collecting role cannot list; see the migration-risk panel.
          </li>
          {accounted !== total ? <li style={{ color: '#c5443f' }}>{total - accounted} extractor rows have a status this strip does not recognise.</li> : null}
        </ul>
      </details>
    </div>
  );
}
