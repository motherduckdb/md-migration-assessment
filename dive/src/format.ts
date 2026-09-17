// Value helpers shared by every panel. DuckDB values arrive as JS numbers,
// strings, or (in the MotherDuck runtime) BigInt / decimal objects, so every
// numeric field goes through N() before it reaches JSX or Recharts.

export const N = (v: unknown): number => (v == null ? 0 : Number(v));
export const S = (v: unknown): string => (v == null ? '' : String(v));

// Binary scale (1 GB = 1024^3 bytes), unit labels per the assessment spec.
const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
export function formatBytes(v: unknown, digits = 1): string {
  if (v == null || Number.isNaN(Number(v))) return '—';
  let n = Math.abs(Number(v));
  let i = 0;
  while (n >= 1024 && i < BYTE_UNITS.length - 1) {
    n /= 1024;
    i++;
  }
  const s = i === 0 ? String(Math.round(n)) : n.toFixed(n >= 100 ? 0 : digits).replace(/\.0$/, '');
  return `${Number(v) < 0 ? '-' : ''}${s} ${BYTE_UNITS[i]}`;
}

export const formatInt = (v: unknown): string => (v == null ? '—' : Math.round(Number(v)).toLocaleString('en-US'));
export const formatCredits = (v: unknown): string =>
  v == null ? '—' : Number(v).toLocaleString('en-US', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
export const formatPct = (v: unknown, digits = 2): string => (v == null ? '—' : `${(Number(v) * 100).toFixed(digits)}%`);

// one decimal, but "1 s" / "24 h" rather than "1.0 s" / "24.0 h" on round values
const one = (n: number) => n.toFixed(1).replace(/\.0$/, '');
export function formatMs(v: unknown): string {
  if (v == null) return '—';
  const ms = Number(v);
  const s = ms / 1000;
  if (s < 1) return `${Math.round(ms)} ms`;
  if (s < 60) return `${one(s)} s`;
  const m = s / 60;
  if (m < 60) return `${one(m)} min`;
  const h = m / 60;
  if (h < 48) return `${one(h)} h`;
  return `${one(h / 24)} d`;
}

export function formatCount(v: unknown, lowerBound = false): string {
  if (v == null) return 'unknown';
  return `${lowerBound ? '≥ ' : ''}${formatInt(v)}`;
}

/** Long rows (key, series, value) -> wide rows keyed by `key`, one column per series. */
export function pivot<T extends Record<string, unknown>>(
  rows: T[],
  key: keyof T,
  series: keyof T,
  value: keyof T,
): Array<Record<string, number | string>> {
  const byKey = new Map<string, Record<string, number | string>>();
  for (const r of rows) {
    const k = S(r[key]);
    if (!byKey.has(k)) byKey.set(k, { [key as string]: k });
    const row = byKey.get(k)!;
    const s = S(r[series]);
    row[s] = N(row[s]) + N(r[value]);
  }
  return [...byKey.values()];
}

/** symlog-style transform for axes that must keep zero on-axis. */
export const symlog = (v: number): number => Math.sign(v) * Math.log10(1 + Math.abs(v));
export const unsymlog = (t: number): number => Math.sign(t) * (Math.pow(10, Math.abs(t)) - 1);

/** Escape a string for a single-quoted SQL literal. */
export const q = (s: string): string => `'${s.replace(/'/g, "''")}'`;
