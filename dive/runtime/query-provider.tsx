/**
 * Local-mode implementation of the `@motherduck/react-sql-query` surface.
 *
 * The Dive source imports `useSQLQuery`, `useDiveState` and `useExport` from
 * that package. In MotherDuck the platform provides them; locally Vite aliases
 * the package to this file, which answers the same API against the loopback
 * query endpoint (`api/query` relative to the page, served by the Vite dev
 * proxy or by `md-assess dashboard`).
 *
 * Kept deliberately close to the production semantics the Dive relies on:
 * `data` is the row array or undefined; results of a previous query stay
 * visible while a new one loads; `useDiveState` persists to the URL hash so a
 * local link reproduces a view.
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { decodeState, encodeState, type StateBag } from './state-codec.ts';

type Row = Record<string, unknown>;
type QueryStatus = 'idle' | 'loading' | 'success' | 'error';

export type ExportQueryOptions = {
  format: 'csv' | 'json' | 'parquet' | 'xlsx';
  title?: string;
  filename?: string;
  [key: string]: unknown;
};

export type UseSQLQueryResult<TData> = {
  data: TData | undefined;
  isLoading: boolean;
  isSuccess: boolean;
  isError: boolean;
  isPlaceholderData: boolean;
  error: Error | null;
  refetch: () => void;
  exportAs: (options: ExportQueryOptions) => Promise<void>;
  status: QueryStatus;
};

type UseSQLQueryOptions<TData> = {
  enabled?: boolean;
  select?: (rows: Row[]) => TData;
  initialData?: TData;
  placeholderData?: TData | ((previous: TData | undefined) => TData | undefined);
};

// ── query endpoint ────────────────────────────────────────────────────────────

function endpoint(name: string): string {
  return new URL(name, document.baseURI).toString();
}

export async function runQuery(sql: string, signal?: AbortSignal): Promise<Row[]> {
  const res = await fetch(endpoint('api/query'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
    signal,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error ?? `query failed (${res.status})`);
  return body.data as Row[];
}

// ── useSQLQuery ───────────────────────────────────────────────────────────────

export function useSQLQuery<TData = Row[]>(
  sql: string,
  options?: UseSQLQueryOptions<TData>,
): UseSQLQueryResult<TData> {
  const enabled = options?.enabled ?? true;
  const select = options?.select;
  const [rows, setRows] = useState<Row[] | undefined>(undefined);
  const [status, setStatus] = useState<QueryStatus>('idle');
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const seq = useRef(0);

  const execute = useCallback(async () => {
    if (!enabled || !sql.trim()) {
      setStatus('idle');
      return;
    }
    const id = ++seq.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus('loading');
    setError(null);
    try {
      const result = await runQuery(sql, controller.signal);
      if (id !== seq.current) return;
      setRows(result);
      setStatus('success');
    } catch (err: unknown) {
      if ((err as Error)?.name === 'AbortError' || id !== seq.current) return;
      setError(err instanceof Error ? err : new Error(String(err)));
      setStatus('error');
    }
  }, [sql, enabled]);

  useEffect(() => {
    execute();
    return () => abortRef.current?.abort();
  }, [execute]);

  const data = useMemo<TData | undefined>(() => {
    if (rows === undefined) return options?.initialData;
    return select ? select(rows) : (rows as unknown as TData);
  }, [rows, select, options?.initialData]);

  const exportAs = useCallback(async (opts: ExportQueryOptions) => {
    console.info('export requested in local mode', { sql, opts });
    window.alert(
      `Exports (${opts.format}) are available when the dashboard runs as a MotherDuck Dive (md-assess publish).`,
    );
  }, [sql]);

  return {
    data,
    isLoading: status === 'loading',
    isSuccess: status === 'success',
    isError: status === 'error',
    isPlaceholderData: false,
    error,
    refetch: execute,
    exportAs,
    status,
  };
}

// ── useDiveState: URL-hash persisted state shared across call sites ───────────

const listeners = new Set<() => void>();
let bag: StateBag = decodeState(window.location.hash);

function writeHash(next: StateBag) {
  bag = next;
  const encoded = encodeState(next);
  const hash = encoded ? `#${encoded}` : '';
  try {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${hash}`);
  } catch {
    // history API unavailable: state still lives in `bag` for this page
  }
  listeners.forEach((l) => l());
}

window.addEventListener('hashchange', () => {
  bag = decodeState(window.location.hash);
  listeners.forEach((l) => l());
});

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useDiveState<T>(key: string, initial: T): [T, (value: T | undefined) => void] {
  const snapshot = useSyncExternalStore(subscribe, () => bag, () => bag);
  const value = (key in snapshot ? snapshot[key] : initial) as T;
  const set = useCallback(
    (v: T | undefined) => {
      const next = { ...bag };
      if (v === undefined) delete next[key];
      else next[key] = v;
      writeHash(next);
    },
    [key],
  );
  return [value, set];
}

// ── useExport ─────────────────────────────────────────────────────────────────

export function useExport() {
  const exportQuery = useCallback(async ({ sql, ...opts }: { sql: string } & ExportQueryOptions) => {
    console.info('export requested in local mode', { sql, opts });
    window.alert(
      `Exports (${opts.format}) are available when the dashboard runs as a MotherDuck Dive (md-assess publish).`,
    );
  }, []);
  return { exportQuery };
}
