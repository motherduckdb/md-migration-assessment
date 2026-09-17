"""`md-assess dashboard` local mode: loopback server over a read-only collection.

Builds a synthetic current-schema collection, starts the server on an
ephemeral port, and checks the contract the Dive runtime relies on: the
random prefix, api/info, api/query JSON conversion, read-only + no external
access, and the schema-version guard.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
import pytest

from md_migration_assessment import META_SCHEMA_VERSION
from md_migration_assessment.dashboard import DashboardServer, open_collection, run_query
from md_migration_assessment.report import REPORT_SCHEMA_VERSION


@pytest.fixture()
def collection(tmp_path) -> Path:
    path = tmp_path / "assessment.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA report; CREATE SCHEMA meta; CREATE SCHEMA raw;")
    con.execute(f"""
        CREATE TABLE meta.collections AS SELECT * FROM (VALUES
          ('0.1.3', {META_SCHEMA_VERSION}, 'standard', 30, 'snowflake', 'LOCATOR1', 'AWS_US_EAST_1', NULL,
           TIMESTAMPTZ '2030-02-01 00:00:00+00', TIMESTAMPTZ '2030-02-01 00:10:00+00')
        ) t(tool_version, meta_schema_version, profile, history_days, source_kind, source_deployment,
            source_region, source_edition, started_at, finished_at)""")
    con.execute("""
        CREATE TABLE meta.extract_runs AS SELECT * FROM (VALUES
          ('tables', 'tables', 'complete', 'account_usage', 3::BIGINT, 'SNOWFLAKE.OBJECT_VIEWER', 'STANDARD', NULL, NULL)
        ) t(extractor, target_table, status, source_used, rows_written, required_privilege, min_edition,
            error_category, error_detail)""")
    con.execute("""
        CREATE TABLE report.feature_inventory AS SELECT * FROM (VALUES
          ('code', 'stored_procedures', 'observed', 7::BIGINT, false, NULL, ['DB_A.S1.P1'], 'procedures', NULL)
        ) t(category, feature, observation_status, count, lower_bound, unknown_reason, sample_objects,
            source_extractor, note)""")
    con.execute(f"""
        CREATE TABLE report.schema_version AS
        SELECT {REPORT_SCHEMA_VERSION} AS report_schema_version, '0.1.3' AS tool_version,
               TIMESTAMPTZ '2030-02-01 00:10:00+00' AS built_at""")
    con.execute("""
        CREATE TABLE report.spend_profile AS SELECT * FROM (VALUES
          ('WH_1', DATE '2030-01-01', 1.5::DECIMAL(18,6), 1.4, 0.1)
        ) t(warehouse_name, usage_date, credits_used, credits_used_compute, credits_used_cloud_services)""")
    con.execute("CREATE TABLE raw.procedures AS SELECT 'SECRET_BODY' AS PROCEDURE_DEFINITION")
    con.close()
    return path


@pytest.fixture()
def server(collection):
    s = DashboardServer(collection, static_dir=collection.parent / "no-static").start()
    yield s
    s.shutdown()


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def _post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_binds_loopback_under_random_prefix(server):
    assert server.url.startswith("http://127.0.0.1:")
    assert len(server.prefix) >= 16
    # the root and unknown prefixes reveal nothing
    base = server.url.split("/" + server.prefix)[0]
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/")
    assert e.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/not-the-prefix/api/info")
    assert e.value.code == 404


def test_info_and_missing_bundle_hint(server, collection):
    status, body = _get(server.url + "api/info")
    info = json.loads(body)
    assert status == 200 and info["mode"] == "local" and info["alias"] == "assessment"
    assert Path(info["database"]) == collection.resolve()
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server.url)
    assert e.value.code == 503 and b"npm run build" in e.value.read()


def test_query_returns_json_safe_rows_with_alias(server):
    status, body = _post(server.url + "api/query", {
        "sql": 'SELECT warehouse_name, usage_date, credits_used, strftime(usage_date, \'%Y-%m-%d\') AS day '
               'FROM "assessment"."report"."spend_profile"'
    })
    assert status == 200
    assert body["columns"] == ["warehouse_name", "usage_date", "credits_used", "day"]
    row = body["data"][0]
    assert row == {"warehouse_name": "WH_1", "usage_date": "2030-01-01", "credits_used": 1.5, "day": "2030-01-01"}


def test_query_errors_are_reported_inline(server):
    status, body = _post(server.url + "api/query", {"sql": "SELECT * FROM assessment.report.nope"})
    assert status == 500 and "nope" in body["error"]
    status, body = _post(server.url + "api/query", {"sql": ""})
    assert status == 400


def test_collection_is_read_only_and_sealed(collection, tmp_path):
    con = open_collection(collection)
    with pytest.raises(duckdb.Error):
        con.execute('CREATE TABLE "assessment"."report"."x" AS SELECT 1')
    with pytest.raises(duckdb.Error):
        con.execute('INSERT INTO "assessment"."report"."spend_profile" SELECT * FROM "assessment"."report"."spend_profile"')
    # no reading other files or attaching anything else once the collection is open
    with pytest.raises(duckdb.Error):
        con.execute(f"ATTACH '{tmp_path / 'other.duckdb'}' AS other")
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{tmp_path / 'x.csv'}')")
    # raw.* is still reachable in local mode (this is the user's own machine); the
    # dashboard's queries simply never reference it — publish uploads the handoff.
    assert run_query(con, 'SELECT count(*) AS n FROM "assessment"."raw"."procedures"')["data"] == [{"n": 1}]
    con.close()


def test_stale_schemas_are_refused(collection):
    con = duckdb.connect(str(collection))
    con.execute("UPDATE meta.collections SET meta_schema_version = meta_schema_version - 1")
    con.close()
    with pytest.raises(ValueError, match="Re-collect"):
        open_collection(collection)
    con = duckdb.connect(str(collection))
    con.execute("UPDATE meta.collections SET meta_schema_version = meta_schema_version + 1")
    con.execute("DROP TABLE report.schema_version")
    con.close()
    with pytest.raises(ValueError, match="md-assess assess"):
        open_collection(collection)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        open_collection(tmp_path / "nope.duckdb")
