#!/usr/bin/env bash
# Vendor the DuckDB parquet extension for the installed DuckDB-WASM build.
#
# DuckDB-WASM does not link the parquet reader statically; by default it would
# fetch it from extensions.duckdb.org at RUNTIME, which this app forbids. This
# script downloads the exact matching extension ONCE, at build time, into
# public/extensions/<duckdb version>/wasm_eh/, and the app points DuckDB at that
# local folder via custom_extension_repository. Needs network only when run.
#
# Usage: bash scripts/vendor-extensions.sh          # download if missing
#        bash scripts/vendor-extensions.sh --check  # fail if missing (used by npm run build)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# DuckDB library version inside the installed wasm build, e.g. v1.4.3
VERSION="$(node -e '
  const path = require("path");
  const root = path.resolve("node_modules/@duckdb/duckdb-wasm");
  const duckdb = require(path.join(root, "dist/duckdb-node-blocking.cjs"));
  (async () => {
    const db = await duckdb.createDuckDB({ eh: { mainModule: path.join(root, "dist/duckdb-eh.wasm"), mainWorker: null } }, new duckdb.VoidLogger(), duckdb.NODE_RUNTIME);
    await db.instantiate(() => {});
    const con = db.connect();
    process.stdout.write(con.query("SELECT version() AS v").toArray()[0].v);
    con.close();
    process.exit(0);
  })().catch(e => { console.error(e); process.exit(1); });
')"
PLATFORM=wasm_eh
DEST="public/extensions/$VERSION/$PLATFORM"
FILE="$DEST/parquet.duckdb_extension.wasm"

if [ -f "$FILE" ]; then
  echo "parquet extension already vendored: $FILE ($(du -h "$FILE" | cut -f1))"
  exit 0
fi
if [ "${1:-}" = "--check" ]; then
  echo "missing $FILE — run: npm run vendor (needs network once)" >&2
  exit 1
fi

# remove extensions for other DuckDB versions so only the matching one ships
rm -rf public/extensions
mkdir -p "$DEST"
URL="https://extensions.duckdb.org/$VERSION/$PLATFORM/parquet.duckdb_extension.wasm"
echo "downloading $URL"
curl -fsSL "$URL" -o "$FILE"
echo "vendored: $FILE ($(du -h "$FILE" | cut -f1))"
