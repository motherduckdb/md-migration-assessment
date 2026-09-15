import { MosaicClient } from '@uwdata/vgplot';
import { el, rows } from './util.js';
import { formatDate, formatDateTime } from '../format.js';

/**
 * Header: what this is, which account, when it was collected, the history window,
 * and what "≥" / unknown / observed-zero mean. Every value is read from the
 * collection metadata and the data itself; nothing is hardcoded.
 */
export class HeaderClient extends MosaicClient {
  constructor(element) {
    super();
    this.element = element;
  }
  query() {
    return `
      SELECT c.*, w.window_start, w.window_end
      FROM collection AS c,
           (SELECT min(usage_date) AS window_start, max(usage_date) AS window_end FROM spend_profile) AS w`;
  }
  queryResult(data) {
    const c = rows(data)[0] ?? {};
    const collected = c.started_at ? formatDateTime(new Date(c.started_at)) : 'unknown date';
    const account = [c.source_deployment, c.source_region].filter(Boolean).join(' · ') || '(account not recorded)';
    const win = c.window_start && c.window_end
      ? `${formatDate(new Date(c.window_start))} → ${formatDate(new Date(c.window_end))}`
      : 'unknown';
    this.element.replaceChildren(
      el('h1', { text: 'Snowflake → MotherDuck migration assessment' }),
      el('p', { class: 'lede' },
        'Local, read-only dashboard over the ',
        el('code', { text: 'md-assess' }),
        ` ${c.tool_version ?? ''} collection (profile ${c.profile ?? 'standard'}) of Snowflake account `,
        el('strong', { text: account }),
        `, collected ${collected}. History window: `,
        el('strong', { text: win }),
        ` (${c.history_days ?? '?'} days). Every count excludes Snowflake system objects unless the
         system-objects toggle is on. Counts marked `,
        el('strong', { text: '≥' }),
        ' are lower bounds limited by what the collecting role could see. Missing evidence is never shown as zero: ',
        el('span', { class: 'chip chip-unknown', text: 'unknown' }),
        ' is a distinct state from ',
        el('span', { class: 'chip chip-zero', text: 'observed zero' }),
        '. Credits are Snowflake credits (no rate is recorded, so no dollar figure is implied); latencies are server-side elapsed time.'
      ),
      el('p', { class: 'warn', text: 'Local-only: this bundle names real databases, schemas, tables, warehouses and tools. Serve it from localhost; do not deploy it.' })
    );
    return this;
  }
}
