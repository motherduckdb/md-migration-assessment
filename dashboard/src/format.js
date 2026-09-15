import { format as d3format, utcFormat } from 'd3';

// Binary scale (1 GB = 1024^3 bytes), unit labels per the assessment spec.
const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
export function formatBytes(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  let n = Math.abs(Number(v));
  let i = 0;
  while (n >= 1024 && i < BYTE_UNITS.length - 1) { n /= 1024; i++; }
  const s = i === 0 ? String(Math.round(n)) : n.toFixed(n >= 100 ? 0 : digits);
  return `${v < 0 ? '-' : ''}${s} ${BYTE_UNITS[i]}`;
}

export const formatInt = d3format(',d');
export const formatSI = d3format('~s');
export const formatCredits = v => (v == null ? '—' : d3format(',.1f')(v));
export const formatPct = d3format('.2%');
export const formatDate = utcFormat('%Y-%m-%d');
export const formatDateTime = utcFormat('%Y-%m-%d %H:%M UTC');

export function formatMs(ms) {
  if (ms == null) return '—';
  const s = ms / 1000;
  if (s < 1) return `${Math.round(ms)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = s / 60;
  if (m < 60) return `${m.toFixed(1)} min`;
  const h = m / 60;
  if (h < 48) return `${h.toFixed(1)} h`;
  return `${(h / 24).toFixed(1)} d`;
}

export function formatCount(v, lowerBound = false) {
  if (v == null) return 'unknown';
  return `${lowerBound ? '≥ ' : ''}${formatInt(v)}`;
}
