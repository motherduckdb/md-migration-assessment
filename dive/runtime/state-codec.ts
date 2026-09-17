/**
 * URL-hash encoding for local-mode `useDiveState`.
 *
 * The hash is `#s=<encodeURIComponent(JSON)>`. Decoding goes through
 * URLSearchParams, which already percent-decodes the value, so the JSON is
 * parsed directly: decoding twice would throw on (or alter) identifiers that
 * legitimately contain `%`, such as a warehouse named `WH%PROD`.
 */
export type StateBag = Record<string, unknown>;

export function encodeState(bag: StateBag): string {
  return Object.keys(bag).length ? `s=${encodeURIComponent(JSON.stringify(bag))}` : '';
}

export function decodeState(hash: string): StateBag {
  try {
    const raw = hash.replace(/^#/, '');
    if (!raw) return {};
    const s = new URLSearchParams(raw).get('s');
    if (!s) return {};
    const value: unknown = JSON.parse(s);
    return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as StateBag) : {};
  } catch {
    return {};
  }
}
