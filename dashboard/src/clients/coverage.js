import { MosaicClient, Query } from '@uwdata/vgplot';
import { el, rows } from './util.js';

/**
 * Coverage strip: from meta.extract_runs / meta.gaps, how many extractors completed,
 * failed, or completed against an Enterprise-only source on a non-Enterprise account
 * (0 rows that cannot be distinguished from "unavailable"). Always visible so the
 * dashboard cannot be read as complete when it isn't.
 */
export class CoverageClient extends MosaicClient {
  constructor(element) {
    super();
    this.element = element;
  }
  query() {
    return Query.from('extract_runs')
      .select('extractor', 'target_table', 'status', 'source_used', 'rows_written',
              'required_privilege', 'min_edition', 'error_category', 'error_detail')
      .orderby('extractor');
  }
  queryResult(data) {
    const runs = rows(data);
    const failed = runs.filter(r => r.status === 'failed');
    // Enterprise-gated sources that returned 0 rows split two ways:
    //  - activity/history sources (ACCESS_HISTORY for table_read_heat): the view exists
    //    but is never populated below Enterprise, so 0 rows is UNMEASURED, not zero.
    //  - object catalogs for Enterprise-only features (masking / row-access policies,
    //    tags): the feature cannot exist on this account, so 0 rows is a real zero.
    const isActivitySource = r => /(_heat|_history|_usage)$/.test(r.extractor) || r.extractor === 'table_read_heat';
    const gated = runs.filter(r =>
      r.status === 'complete' && Number(r.rows_written ?? 0) === 0 && r.min_edition === 'ENTERPRISE');
    const editionLimited = gated.filter(isActivitySource);
    const gatedZero = gated.filter(r => !isActivitySource(r));
    const complete = runs.filter(r => r.status === 'complete' && !editionLimited.includes(r));
    const total = runs.length;

    const seg = (cls, n, label, title) =>
      n === 0 ? null : el('span', { class: `cov-seg ${cls}`, style: `flex:${n}`, title }, `${n} ${label}`);

    const detail = el('details', { class: 'cov-detail' },
      el('summary', { text: 'extractor detail' }),
      el('ul', {},
        failed.map(r => el('li', {},
          el('span', { class: 'chip chip-failed', text: 'failed' }), ' ',
          el('code', { text: r.extractor }),
          ` — ${r.error_detail ?? r.error_category ?? 'error'}`)),
        editionLimited.map(r => el('li', {},
          el('span', { class: 'chip chip-unknown', text: 'edition-limited → unknown' }), ' ',
          el('code', { text: r.extractor }),
          ` — completed with 0 rows from an Enterprise-only activity source (${r.source_used ?? 'account_usage'}); on this non-Enterprise account that is unmeasured, not zero.`,
          r.extractor === 'table_read_heat'
            ? ' ACCESS_HISTORY is never populated below Enterprise, so there is no table read-heat evidence — do not read this as "no reads".'
            : '')),
        gatedZero.map(r => el('li', {},
          el('span', { class: 'chip chip-zero', text: 'observed zero' }), ' ',
          el('code', { text: r.extractor }),
          ' — 0 rows; the feature itself requires Enterprise, so zero objects is expected and real on this account.')),
        el('li', {}, el('span', { class: 'chip chip-zero', text: 'not visible' }), ' ',
          'Feature-inventory rows marked unknown / not_visible come from catalogs the collecting role cannot list; see the migration-risk panel.')
      ));

    this.element.replaceChildren(
      el('div', { class: 'cov-title' },
        el('strong', { text: 'Collection coverage' }),
        ` · ${total} extractors`,
        el('span', { class: 'cov-note', text: 'Charts below only cover what the collector could measure.' })),
      el('div', { class: 'cov-bar' },
        seg('cov-ok', complete.length, 'complete', 'completed with evidence'),
        seg('cov-edition', editionLimited.length, 'edition-limited → unknown',
          editionLimited.map(r => r.extractor).join(', ')),
        seg('cov-failed', failed.length, 'failed', failed.map(r => r.extractor).join(', '))),
      detail
    );
    return this;
  }
}
