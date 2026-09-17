// MotherDuck palette for series (Dive design guide), plus the semantic colours
// the assessment contract needs: storage components, query classes, and the
// three observation states.

export const SERIES = ['#0777b3', '#bd4e35', '#2d7a00', '#e18727', '#638cad', '#adadad', '#7b5ea7', '#2a9d8f', '#c77dff', '#8a5a44'];

export const INK = '#231f20';
export const MUTED = '#6a6a6a';
export const LINE = '#e5e5e5';
export const BG = '#f8f8f8';
export const PRIMARY = '#0777b3';
export const NEGATIVE = '#bc1200';
export const POSITIVE = '#2d7a00';
export const WARN_BG = '#fff4e5';
export const WARN_INK = '#7a4b00';

/** Stable colour per entity, assigned in importance order at startup. */
export function seriesColor(domain: string[]): (name: string) => string {
  const idx = new Map(domain.map((d, i) => [d, i]));
  return (name) => SERIES[(idx.get(name) ?? domain.length) % SERIES.length];
}

// storage components: active is the migrated footprint; fail-safe vanishes on migration
export const STORAGE = {
  order: ['active', 'time_travel', 'failsafe', 'retained_for_clone'] as const,
  color: { active: '#3b6ea5', time_travel: '#8fb3d9', failsafe: '#e0a458', retained_for_clone: '#c9c9d1' } as Record<string, string>,
  label: {
    active: 'active',
    time_travel: 'time travel',
    failsafe: 'fail-safe (not migrated)',
    retained_for_clone: 'retained for clones',
  } as Record<string, string>,
};

export const QUERY_CLASSES = ['transformation', 'read', 'file operation', 'metadata / session', 'ddl / admin'];

export const LOAD_METHOD = { copy_into: '#3b6ea5', snowpipe: '#edae49' } as Record<string, string>;
export const CONFIDENCE = { high: '#3b6ea5', medium: '#edae49', low: '#9aa0a6' } as Record<string, string>;

export const STATUS = {
  observed: '#3b6ea5',
  observed_zero: '#9aa0a6',
  unknown: 'repeating-linear-gradient(45deg, #e9e9ee 0 4px, #8a8a96 4px 6px)',
};
