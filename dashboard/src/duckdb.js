// DuckDB-WASM bootstrap with every runtime asset vendored by Vite.
// Single-threaded "eh" bundle: no SharedArrayBuffer, no COOP/COEP requirement.
// Nothing here references jsDelivr or any other remote origin.
import * as duckdb from '@duckdb/duckdb-wasm';
import wasmUrl from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url';
import workerUrl from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url';

export async function createDuckDB({ log = false } = {}) {
  const worker = new Worker(new URL(workerUrl, import.meta.url));
  const logger = log ? new duckdb.ConsoleLogger() : new duckdb.VoidLogger();
  const db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(new URL(wasmUrl, import.meta.url).href);
  return db;
}

/** Resolve a bundle-relative path (e.g. "data/x.parquet") against the page URL. */
export function assetUrl(relative) {
  return new URL(relative, document.baseURI).href;
}

/**
 * Fetch a parquet file from the bundle and register its bytes with DuckDB under
 * `name`, so `read_parquet('<name>')` works regardless of the static server's
 * range-request support and without the worker resolving relative URLs itself.
 */
export async function registerParquet(db, name, relativePath) {
  const res = await fetch(assetUrl(relativePath));
  if (!res.ok) throw new Error(`failed to fetch ${relativePath}: ${res.status}`);
  const buf = new Uint8Array(await res.arrayBuffer());
  const size = buf.byteLength; // the buffer is transferred (detached) by registerFileBuffer
  await db.registerFileBuffer(name, buf);
  return size;
}
