// Small presentational pieces shared by the panels. Inline styles only: the
// Dive runtime has no external stylesheet, and local mode must not fetch one.
import type { CSSProperties, ReactNode } from 'react';
import { INK, LINE, MUTED, NEGATIVE, WARN_BG, WARN_INK } from './palette';

export const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';
export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

/** The one stylesheet the Dive carries, rendered by the root component. */
export const CSS = `
  .mda { font-family: ${FONT}; color: ${INK}; font-size: 14px; line-height: 1.45; }
  .mda code { font-family: ${MONO}; font-size: 0.92em; }
  .mda .panel { background: #fff; border: 1px solid ${LINE}; border-radius: 8px; padding: 14px 16px 10px; }
  .mda .panel h2 { font-size: 16px; margin: 0 0 4px; font-weight: 600; }
  .mda .panel h3 { font-size: 12px; margin: 8px 0 2px; color: ${MUTED}; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
  .mda .panel .sub { margin: 0 0 8px; color: ${MUTED}; font-size: 13px; max-width: 1100px; }
  .mda .chip { display: inline-block; padding: 0 6px; border-radius: 4px; font-size: 12px; font-weight: 600; line-height: 18px; vertical-align: middle; }
  .mda .chip-unknown { background: repeating-linear-gradient(45deg, #e9e9ee 0 3px, #b9b9c4 3px 5px); color: #333; }
  .mda .chip-zero { border: 1.5px solid #9aa0a6; color: ${MUTED}; }
  .mda .chip-failed { background: ${NEGATIVE}; color: #fff; }
  .mda .chip-ok { background: #2d7a00; color: #fff; }
  .mda .cov-seg { display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; color: #fff; padding: 0 6px; white-space: nowrap; overflow: hidden; min-width: max-content; }
  .mda details summary { cursor: pointer; }
  .mda .feat-row { display: grid; grid-template-columns: minmax(160px, 220px) 1fr 92px; gap: 8px; align-items: center; padding: 1px 0; font-size: 12px; }
  .mda .feat-row:hover { background: #f1f4f8; }
  .mda .feat-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mda .feat-bar { height: 12px; background: #f1f2f5; border-radius: 2px; position: relative; overflow: hidden; }
  .mda .feat-fill { height: 100%; border-radius: 2px; }
  .mda .feat-observed { background: #3b6ea5; }
  .mda .feat-zero { width: 100%; background: transparent; border: 1.5px dashed #9aa0a6; border-radius: 2px; position: relative; box-sizing: border-box; }
  .mda .feat-zero-tick { position: absolute; left: 0; top: -2px; bottom: -2px; width: 3px; background: #9aa0a6; }
  .mda .feat-unknown { background: repeating-linear-gradient(45deg, #e9e9ee 0 4px, #8a8a96 4px 6px); display: flex; align-items: center; justify-content: center; }
  .mda .feat-unknown-label { font-size: 10px; font-weight: 700; color: #2b2b33; background: rgba(255,255,255,.85); padding: 0 6px; border-radius: 3px; line-height: 12px; }
  .mda .feat-nr { background: #e3e5ea; border: 1px solid #c9c9d1; box-sizing: border-box; display: flex; align-items: center; justify-content: center; }
  .mda .feat-nr-label { font-size: 10px; font-weight: 600; color: #5d6270; line-height: 12px; }
  .mda .feat-value { text-align: right; font-variant-numeric: tabular-nums; }
  .mda .feat-lb { color: ${WARN_INK}; font-weight: 600; }
  .mda .bar-click { cursor: pointer; }
  .mda select, .mda button, .mda input { font: inherit; }
  .mda .btn { padding: 4px 10px; border: 1px solid ${LINE}; background: #fff; border-radius: 6px; cursor: pointer; }
  .mda .btn:hover { border-color: #0777b3; }
  .mda .btn.on { background: #0777b3; color: #fff; border-color: #0777b3; }
  .mda .recharts-wrapper { font-size: 11px; }
  .mda .skeleton { background: #eef0f3; border-radius: 6px; animation: mda-pulse 1.4s ease-in-out infinite; }
  @keyframes mda-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .55; } }
`;

export function Panel({ title, sub, children, style }: { title: string; sub?: ReactNode; children: ReactNode; style?: CSSProperties }) {
  return (
    <section className="panel" style={style}>
      <h2>{title}</h2>
      {sub ? <p className="sub">{sub}</p> : null}
      {children}
    </section>
  );
}

export function Skeleton({ height = 240 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-hidden="true" />;
}

export function ErrorNote({ error }: { error: unknown }) {
  return (
    <p role="alert" style={{ color: NEGATIVE, fontSize: 12, whiteSpace: 'pre-wrap' }}>
      {String((error as Error)?.message ?? error)}
    </p>
  );
}

export function Warn({ children }: { children: ReactNode }) {
  return (
    <p style={{ margin: '0 0 12px', padding: '6px 10px', background: WARN_BG, color: WARN_INK, borderRadius: 6, fontWeight: 600 }}>
      {children}
    </p>
  );
}

/** Two-column row that collapses on narrow viewports (the Dive frame is ~800px). */
export function Row({ children, min = 380 }: { children: ReactNode; min?: number }) {
  return <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: 20, alignItems: 'start' }}>{children}</div>;
}

/** Generic tooltip body: label + rows of (swatch, name, formatted value). */
export function TipBox({ title, rows }: { title?: ReactNode; rows: Array<{ color?: string; name: string; value: string }> }) {
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 6, padding: '6px 10px', fontSize: 12, boxShadow: '0 2px 8px rgba(0,0,0,.08)' }}>
      {title ? <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div> : null}
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {r.color ? <span style={{ width: 10, height: 10, background: r.color, borderRadius: 2, display: 'inline-block' }} /> : null}
          <span style={{ color: MUTED }}>{r.name}</span>
          <span style={{ marginLeft: 'auto', fontVariantNumeric: 'tabular-nums' }}>{r.value}</span>
        </div>
      ))}
    </div>
  );
}
