// One stable colour per warehouse and per database, shared by every view.
// Domains are ordered by importance (credits / active bytes) at startup so the
// most prominent entities get the most distinguishable hues.
import { schemeTableau10, schemeObservable10 } from 'd3';

const DB_SCHEME = [
  '#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1',
  '#ff9da7', '#9c755f', '#bab0ac', '#1f77b4', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf', '#aec7e8'
];

export function makePalette(warehouses, databases) {
  const whRange = warehouses.map((_, i) => schemeObservable10[i % 10]);
  const dbRange = databases.map((_, i) => DB_SCHEME[i % DB_SCHEME.length]);
  return {
    warehouse: { domain: warehouses, range: whRange },
    database: { domain: databases, range: dbRange },
    // storage components: active is the migrated footprint; fail-safe vanishes on migration
    storage: {
      domain: ['active', 'time_travel', 'failsafe', 'retained_for_clone'],
      range: ['#3b6ea5', '#8fb3d9', '#e0a458', '#c9c9d1'],
      labels: {
        active: 'active',
        time_travel: 'time travel',
        failsafe: 'fail-safe (not migrated)',
        retained_for_clone: 'retained for clones'
      }
    },
    queryClass: {
      domain: ['transformation', 'read', 'file operation', 'metadata / session', 'ddl / admin'],
      range: ['#d1495b', '#00798c', '#edae49', '#9aa0a6', '#66a182']
    },
    loadMethod: { domain: ['copy_into', 'snowpipe'], range: ['#3b6ea5', '#edae49'] },
    status: {
      observed: '#3b6ea5',
      observed_zero: '#9aa0a6',
      unknown: 'url(#hatch)'
    },
    ...schemeTableau10 && {}
  };
}
