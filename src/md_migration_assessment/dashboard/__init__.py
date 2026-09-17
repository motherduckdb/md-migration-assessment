"""Local mode for the dashboard: ``md-assess dashboard``.

Serves the dashboard bundle (the Dive source under a small runtime, built by
``npm run build`` in ``dive/``) from a loopback HTTP server and answers its
``api/query`` calls against the assessment database, which is attached
``READ_ONLY`` under the alias the Dive declares (``assessment``).

Nothing leaves the machine: the browser talks only to ``127.0.0.1``, DuckDB
has external access disabled once the file is attached, and the whole site
lives under a per-launch random path so another local page cannot guess the
endpoint.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import math
import secrets
import sys
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb

from .. import db as _db
from ..report import check_report_version

ALIAS = "assessment"
STATIC_DIR = Path(__file__).resolve().parent / "static"
BUILD_HINT = "the dashboard bundle is not built; run `npm install && npm run build` in dive/"

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json",
}


def open_collection(db_path: Path) -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB with the collection attached read-only as ``assessment``.

    Raises ``FileNotFoundError`` when the file is missing and ``ValueError``
    (with the CLI's own wording) when its meta or report schema is not the one
    this tool reads.
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"assessment database not found: {db_path}")
    con = duckdb.connect()
    # ATTACH takes no bound parameters; quote the path as a SQL literal.
    path_literal = str(db_path).replace("'", "''")
    con.execute(f"ATTACH '{path_literal}' AS {ALIAS} (READ_ONLY)")
    con.execute(f"USE {ALIAS}")
    # The version checks read unqualified meta.* / report.*; USE above resolves them.
    _db.check_meta_version(con)
    if not check_report_version(con, str(db_path)):
        raise ValueError(
            f"{db_path} has no report.* layer yet; build it with: md-assess assess --db {db_path}"
        )
    # Everything the dashboard needs is attached; forbid any further file or
    # network access from SQL for the lifetime of this process.
    con.execute("SET enable_external_access = false")
    return con


def _json_value(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return base64.b64encode(bytes(v)).decode("ascii")
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_value(x) for k, x in v.items()}
    return str(v)


def run_query(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    """Execute ``sql`` on a cursor of ``con`` and return JSON-safe rows."""
    cur = con.cursor()
    try:
        cur.execute(sql)
        columns = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    finally:
        cur.close()
    return {
        "columns": columns,
        "data": [{c: _json_value(v) for c, v in zip(columns, row)} for row in rows],
    }


class DashboardServer:
    """Loopback server: static bundle + ``api/query`` + ``api/info`` under a random prefix."""

    def __init__(
        self,
        db_path: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        static_dir: Path = STATIC_DIR,
        prefix: str | None = None,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.static_dir = static_dir
        self.prefix = prefix if prefix is not None else secrets.token_urlsafe(12)
        self.con = open_collection(self.db_path)
        self.tool_version = _db.__version__
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "md-assess"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
                pass

            def _send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: HTTPStatus, obj: Any) -> None:
                self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

            def _route(self) -> str | None:
                path = self.path.split("?", 1)[0]
                root = f"/{server.prefix}"
                if path == root:
                    return ""
                if path.startswith(root + "/"):
                    return path[len(root) + 1 :]
                return None

            def do_GET(self) -> None:  # noqa: N802
                rel = self._route()
                if rel is None:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                if rel == "api/info":
                    self._json(
                        HTTPStatus.OK,
                        {"mode": "local", "database": str(server.db_path), "alias": ALIAS, "tool_version": server.tool_version},
                    )
                    return
                if rel in ("", "index.html"):
                    rel = "index.html"
                target = (server.static_dir / rel).resolve()
                try:
                    target.relative_to(server.static_dir.resolve())
                except ValueError:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                if not target.is_file():
                    if rel == "index.html":
                        self._send(HTTPStatus.SERVICE_UNAVAILABLE, BUILD_HINT.encode(), "text/plain; charset=utf-8")
                    else:
                        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                self._send(HTTPStatus.OK, target.read_bytes(), _MIME.get(target.suffix, "application/octet-stream"))

            def do_POST(self) -> None:  # noqa: N802
                rel = self._route()
                if rel != "api/query":
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    sql = body.get("sql")
                    if not isinstance(sql, str) or not sql.strip():
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "missing sql"})
                        return
                    self._json(HTTPStatus.OK, run_query(server.con, sql))
                except Exception as exc:  # the browser shows the message inline
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc).splitlines()[0][:2000]})

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/{self.prefix}/"

    @property
    def bundle_present(self) -> bool:
        return (self.static_dir / "index.html").is_file()

    def start(self) -> "DashboardServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.con.close()


def serve(db_path: Path, *, port: int = 0, open_browser: bool = True) -> None:
    """Run the local dashboard until Ctrl+C."""
    server = DashboardServer(db_path, port=port)
    # flush: the process then blocks in serve_forever, and a user who piped or
    # redirected stdout would otherwise never see the URL
    if not server.bundle_present:
        print(f"warning: {BUILD_HINT}", file=sys.stderr, flush=True)
    print(f"md-assess dashboard: {server.url}", flush=True)
    print(
        f"  reading {server.db_path} read-only over loopback; nothing leaves this machine. Ctrl+C to stop.",
        flush=True,
    )
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
        server.con.close()
