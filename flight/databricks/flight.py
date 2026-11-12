"""Run the md-assess migration collector against a Databricks workspace on
MotherDuck compute, then publish the redacted inventory and its Dive.

Adapted from the Snowflake recipe (motherduck-cookbook/flight-plans/
flight-snowflake-migration-assessment). Differences:

- Databricks credentials: a service principal (DATABRICKS_CLIENT_ID and
  DATABRICKS_CLIENT_SECRET, OAuth M2M) or a personal access token
  (DATABRICKS_TOKEN), from the Flights secret named by SECRET_NAME.
- The collector's Databricks adapter is not on a public release yet, so the
  wheel is read from a MotherDuck table (WHEEL_DB.main.wheels) and unpacked on
  /tmp instead of being installed from requirements.txt. Once a release
  exists, pin it in requirements.txt and drop load_collector().
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import duckdb

IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CREDENTIAL_PARAMS = ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN")
PKG_DIR = Path("/tmp/md-assess-pkg")


def main() -> None:
    """Collect from Databricks into a temp file, then publish only the redacted database."""
    # The Flight container has no local time zone; pin UTC before any DuckDB
    # connection opens here or in the md-assess subprocesses.
    os.environ["TZ"] = "UTC"
    time.tzset()

    profile = env("PROFILE", "standard").lower()
    if profile not in {"lite", "standard"}:
        raise ValueError(f"PROFILE must be 'lite' or 'standard', got {profile!r}")
    history_days = int(env("HISTORY_DAYS", "30") or "30")
    if not 1 <= history_days <= 365:
        raise ValueError(f"HISTORY_DAYS must be between 1 and 365, got {history_days}")
    scope = [s.strip() for s in env("SCOPE", "").split(",") if s.strip()]
    target_db_raw = env("TARGET_DB", "")
    target_db = validate_identifier("TARGET_DB", target_db_raw) if target_db_raw else ""
    dive_title = env("DIVE_TITLE", "")
    replace = env_bool("REPLACE", True)

    resolve_databricks_credentials(env("SECRET_NAME", "databricks_creds"))
    load_collector(validate_identifier("WHEEL_DB", env("WHEEL_DB", "md_assess_dist")),
                   env("WHEEL_VERSION", ""))

    if target_db:
        guard_existing_database(target_db, replace)

    # The full collection only exists on this run's /tmp and is deleted at the
    # end. Only the redacted database that `md-assess publish` builds is kept.
    workdir = Path(tempfile.mkdtemp(prefix="md-assess-", dir="/tmp"))
    try:
        collection = workdir / "assessment.duckdb"
        collect(collection, profile, history_days, scope)
        if target_db:
            database = target_db
        else:
            from md_migration_assessment.publish import database_name_for
            database = database_name_for(collection_deployment(collection))
            guard_existing_database(database, replace)
        publish(collection, database, dive_title, replace)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def load_collector(wheel_db: str, version: str) -> None:
    """Read the collector wheel from MotherDuck, check its hash, unpack it."""
    con = duckdb.connect(f"md:{wheel_db}?attach_mode=single")
    try:
        sql = "SELECT version, filename, sha256, content FROM main.wheels"
        params: list = []
        if version:
            sql += " WHERE version = ?"
            params.append(version)
        row = con.execute(sql + " ORDER BY uploaded_at DESC LIMIT 1", params).fetchone()
    finally:
        con.close()
    if not row:
        raise RuntimeError(f"no collector wheel in {wheel_db}.main.wheels"
                           + (f" for version {version}" if version else ""))
    ver, filename, sha256, content = row
    if hashlib.sha256(content).hexdigest() != sha256:
        raise RuntimeError(f"wheel {filename} failed its sha256 check")
    shutil.rmtree(PKG_DIR, ignore_errors=True)
    PKG_DIR.mkdir(parents=True)
    whl = PKG_DIR / filename
    whl.write_bytes(content)
    with zipfile.ZipFile(whl) as zf:
        zf.extractall(PKG_DIR)
    sys.path.insert(0, str(PKG_DIR))
    os.environ["PYTHONPATH"] = str(PKG_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
    print(f"collector: md-migration-assessment {ver} ({filename}, sha256 {sha256[:12]})")


def md_assess(*args: str) -> list[str]:
    """Command line that runs the md-assess app from the unpacked wheel."""
    # The wheel is unpacked, not installed, so there is no md-assess console
    # script; call the Typer app directly.
    return [sys.executable, "-c",
            "from md_migration_assessment.cli import app; app()", *args]


def collect(collection: Path, profile: str, history_days: int, scope: list[str]) -> None:
    """Run the Databricks collection and write everything it reads to a local file."""
    args = md_assess("collect", "--source", "databricks", "--profile", profile,
                     "--history-days", str(history_days), "--output", str(collection))
    for entry in scope:
        args += ["--scope", entry]
    print(f"collect: profile={profile} history_days={history_days} "
          f"scope={scope or 'metastore-wide'}", flush=True)
    subprocess.run(args, check=True)


def collection_deployment(collection: Path) -> str | None:
    """Workspace host recorded by the latest collection, or None if there is none."""
    con = duckdb.connect(str(collection), read_only=True)
    try:
        row = con.execute("SELECT source_deployment FROM meta.collections "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return row[0] if row else None


def guard_existing_database(database: str, replace: bool) -> None:
    """Stop unless the target database is absent or an earlier assessment that may be replaced."""
    # Refuse to drop anything that is not an earlier assessment.
    con = duckdb.connect("md:")
    try:
        exists = con.execute("SELECT count(*) FROM duckdb_databases() "
                             "WHERE lower(database_name) = lower(?)", [database]).fetchone()[0]
        if not exists:
            return
        if not replace:
            raise RuntimeError(f"MotherDuck database {database!r} already exists and "
                               "REPLACE=false; set REPLACE=true or pick another TARGET_DB")
        is_assessment = con.execute(
            "SELECT count(*) FROM duckdb_tables() WHERE lower(database_name) = lower(?) "
            "AND schema_name = 'meta' AND table_name = 'collections'", [database]).fetchone()[0]
        if not is_assessment:
            raise RuntimeError(f"MotherDuck database {database!r} exists but is not an earlier "
                               "assessment (no meta.collections table); refusing to replace it")
        print(f"publish: replacing earlier assessment database {database!r}")
    finally:
        con.close()


def publish(collection: Path, database: str, dive_title: str, replace: bool) -> None:
    """Publish the redacted database and its Dive, then print what was kept or dropped."""
    # Databricks variables are removed first: publish needs only MOTHERDUCK_TOKEN.
    pub_env = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")
               and not any(k.endswith("_" + p) for p in CREDENTIAL_PARAMS)}
    args = md_assess("publish", "--db", str(collection), "--name", database, "--json")
    if dive_title:
        args += ["--title", dive_title]
    if replace:
        args.append("--replace")
    result = subprocess.run(args, capture_output=True, text=True, env=pub_env)
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode != 0:
        print(result.stdout, end="")
        raise RuntimeError(f"md-assess publish failed with exit code {result.returncode}")
    s = json.loads(result.stdout)
    print(f"publish: database md:{s['database']} ({s['handoff_tables']} tables, "
          f"{s['handoff_rows']:,} rows)")
    print(f"publish: Dive {s['dive_title']!r} "
          f"{'created' if s['dive_created'] else 'updated'}: {s['dive_url']}")
    for cls, cols in sorted(s["sensitive_included"].items()):
        print(f"publish: disclosed {cls}: {len(cols)} column(s)")
    if s["excluded_columns"]:
        print(f"publish: excluded columns: {', '.join(s['excluded_columns'])}")
    if s["unclassified_included"]:
        print(f"publish: unclassified columns included: {len(s['unclassified_included'])}")
    if s["dropped_unexpected"]:
        tables = sorted({c.rsplit('.', 1)[0] for c in s["dropped_unexpected"]})
        print(f"publish: WARNING dropped {len(s['dropped_unexpected'])} unexpected "
              f"column(s) in {len(tables)} table(s): {', '.join(tables)}")
    if s["skipped_raw_tables"]:
        print(f"publish: WARNING skipped raw tables: {', '.join(s['skipped_raw_tables'])}")


def resolve_databricks_credentials(secret_name: str) -> None:
    """Check the Databricks settings are present and set the names md-assess reads."""
    # Copy each credential from the namespaced secret param onto the bare name
    # md-assess reads, so a raw alias from another secret cannot win.
    for param in CREDENTIAL_PARAMS:
        value = os.environ.get(f"{secret_name}_{param}")
        if value:
            os.environ[param] = value
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    if not host:
        raise ValueError("DATABRICKS_HOST not set: put the workspace URL in Flight config")
    if not (os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ.get("DATABRICKS_HTTP_PATH")):
        raise ValueError("set DATABRICKS_WAREHOUSE_ID (or DATABRICKS_HTTP_PATH) in Flight config")
    m2m = os.environ.get("DATABRICKS_CLIENT_ID") and os.environ.get("DATABRICKS_CLIENT_SECRET")
    if not (m2m or os.environ.get("DATABRICKS_TOKEN")):
        # Without these the connector would try browser sign-in, which a Flight can't do.
        raise ValueError(f"no Databricks credential: add DATABRICKS_CLIENT_ID and "
                         f"DATABRICKS_CLIENT_SECRET (or DATABRICKS_TOKEN) to the "
                         f"{secret_name!r} Flights secret")
    print(f"auth: {'service principal (OAuth M2M)' if m2m else 'personal access token'} "
          f"against {host}")


def env(name: str, default: str) -> str:
    """Read an environment variable, trimmed, with a default."""
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool) -> bool:
    """Read a yes/no environment variable. Unset or empty gives the default."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_identifier(name: str, value: str) -> str:
    """Return the value if it is a plain database name, else raise."""
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must match {IDENTIFIER_RE.pattern}, got {value!r}")
    return value


if __name__ == "__main__":
    main()
