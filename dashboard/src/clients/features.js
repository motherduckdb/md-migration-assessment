import { MosaicClient, Query } from '@uwdata/vgplot';
import { el, rows } from './util.js';
import { formatCount } from '../format.js';

const REASON = {
  not_visible: 'not visible to the collecting role',
  probe_failed: 'the probe query failed',
  extract_failed: 'the extractor failed'
};

/**
 * Feature inventory grouped by category. Honours the collector's contract:
 *   observed       → solid bar proportional to count (symlog), "≥" when lower_bound
 *   observed_zero  → explicit zero marker with an outline bar, labelled "0 observed"
 *   unknown        → full-width hatched grey bar, reason on hover, no number
 * A bar of height 0 for observed_zero must never look like unknown, so unknown gets
 * its own hatched shape rather than an empty bar.
 */
export class FeatureInventoryClient extends MosaicClient {
  constructor(element) {
    super();
    this.element = element;
  }
  query() {
    return Query.from('feature_inventory')
      .select('category', 'feature', 'observation_status', 'count', 'lower_bound',
              'unknown_reason', 'sample_objects', 'source_extractor', 'note')
      .orderby('category', 'feature');
  }
  queryResult(data) {
    const feats = rows(data);
    const max = Math.max(1, ...feats.map(f => Number(f.count ?? 0)));
    const scale = v => Math.log1p(Number(v)) / Math.log1p(max); // symlog-ish share of width

    const byCat = new Map();
    for (const f of feats) {
      if (!byCat.has(f.category)) byCat.set(f.category, []);
      byCat.get(f.category).push(f);
    }

    const legend = el('div', { class: 'feat-legend' },
      el('span', {}, el('i', { class: 'sw sw-observed' }), ' observed'),
      el('span', {}, el('i', { class: 'sw sw-zero' }), ' observed zero'),
      el('span', {}, el('i', { class: 'sw sw-unknown' }), ' unknown (hover for reason)'),
      el('span', {}, el('b', { text: '≥' }), ' lower bound'));

    const sections = [...byCat.entries()].map(([cat, list]) => {
      const n = list.length;
      const unknown = list.filter(f => f.observation_status === 'unknown').length;
      const observed = list.filter(f => f.observation_status === 'observed').length;
      return el('section', { class: 'feat-cat' },
        el('h4', {},
          el('span', { text: cat.replace('_', ' ') }),
          el('span', { class: 'feat-cat-meta', text: `${observed} observed · ${n - observed - unknown} zero · ${unknown} unknown` })),
        el('div', { class: 'feat-rows' }, list.map(f => this.row(f, scale))));
    });

    this.element.replaceChildren(legend, ...sections);
    return this;
  }

  row(f, scale) {
    const status = f.observation_status;
    const name = el('span', { class: 'feat-name', text: f.feature.replaceAll('_', ' ') });
    const bar = el('div', { class: 'feat-bar' });
    let value;
    let title;

    if (status === 'unknown') {
      const reason = f.unknown_reason ?? 'unknown';
      title = `UNKNOWN — ${REASON[reason] ?? reason}` +
        (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') +
        (f.note ? `\n${f.note}` : '') +
        '\nNot measured. This is not a zero.';
      bar.append(el('div', { class: 'feat-fill feat-unknown', style: 'width:100%' },
        el('span', { class: 'feat-unknown-label', text: `unknown · ${reason.replaceAll('_', ' ')}` })));
      value = el('span', { class: 'feat-value feat-value-unknown', text: '?' });
    } else if (status === 'observed_zero') {
      title = `OBSERVED ZERO — the extractor ran and found none.` +
        (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') +
        (f.note ? `\n${f.note}` : '');
      bar.append(el('div', { class: 'feat-fill feat-zero' }, el('span', { class: 'feat-zero-tick' })));
      value = el('span', { class: 'feat-value feat-value-zero', text: '0 observed' });
    } else {
      const w = Math.max(1.5, 100 * scale(f.count));
      title = `OBSERVED — ${formatCount(f.count, f.lower_bound)}` +
        (f.lower_bound ? '\nlower bound: the collecting role may not see everything' : '') +
        (f.sample_objects ? `\nsamples: ${f.sample_objects}` : '') +
        (f.source_extractor ? `\nextractor: ${f.source_extractor}` : '') +
        (f.note ? `\n${f.note}` : '');
      bar.append(el('div', { class: 'feat-fill feat-observed', style: `width:${w}%` }));
      value = el('span', { class: `feat-value${f.lower_bound ? ' feat-lb' : ''}`, text: formatCount(f.count, f.lower_bound) });
    }

    return el('div', { class: `feat-row feat-row--${status}`, title, 'data-feature': f.feature }, name, bar, value);
  }
}
