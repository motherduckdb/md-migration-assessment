"""Seed a Snowflake test account with objects covering every M1 extractor.

Usage (with SNOWFLAKE_* env vars set, role must be able to create databases —
ACCOUNTADMIN on a trial):

    set -a; source .env.snowflake; set +a
    .venv/bin/python tests/integration/seed.py

Statements are a Python list (not a .sql file) because UDF/procedure bodies
contain semicolons and $$-quoting that naive statement splitting mangles.
Each statement runs independently. Statements matching OPTIONAL below are
allowed to fail (edition-dependent features on Standard-edition accounts);
any other failure makes the seed exit non-zero, so a broken fixture cannot
silently pass the live suite.

NOTE: the search-optimization statement starts background index maintenance
that consumes credits and persists until DROPped with the table.

Note: ACCOUNT_USAGE views lag by ~45min-2h. INFORMATION_SCHEMA (lite profile)
sees these objects immediately; run the standard profile after the lag.
"""

from __future__ import annotations

import re
import sys

from md_migration_assessment.sources.snowflake.connection import SnowflakeConfig

#: Edition-dependent statements: statement-substring -> (reason, error regex).
#: A failure is optional ONLY when the statement is a candidate AND its error
#: positively matches that candidate's edition-unavailability shape — the
#: generic "Unsupported feature" wording alone is NOT edition-specific in
#: Snowflake (it also covers invalid combinations and operational
#: restrictions), so each candidate whitelists the exact feature token, plus
#: the explicitly edition-worded variants.
_EE = r"(?:enterprise|higher) edition"


def _uf(tokens: str) -> str:
    """Exact unsupported-feature error shape: the token must be terminated by
    its closing quote — 'ROW ACCESS POLICY ON EXTERNAL TABLE' must NOT match
    the ROW ACCESS POLICY candidate (that is an operational restriction, not
    an edition gap)."""
    return rf"unsupported feature\s*'(?:{tokens})'"


EDITION_DEPENDENT: dict[str, tuple[str, str]] = {
    "MATERIALIZED VIEW": (
        "requires Enterprise edition",
        rf"{_uf('materialized view')}|{_EE}",
    ),
    "MASKING POLICY": (
        "requires Enterprise edition",
        rf"{_uf('column security|masking policy')}|{_EE}",
    ),
    "ROW ACCESS POLICY": (
        "requires Enterprise edition",
        rf"{_uf('row access policy')}|{_EE}",
    ),
    "TAG ": (
        "object tagging requires Enterprise edition",
        rf"{_uf('tag')}|{_EE}",
    ),
    "MDA_MULTI_WH": (
        # no reliable feature token: only explicit edition wording is accepted
        "multi-cluster warehouses require Enterprise edition",
        _EE,
    ),
    "SEARCH OPTIMIZATION": (
        "requires Enterprise edition (and consumes credits)",
        rf"{_uf('search optimization')}|{_EE}",
    ),
}


def _optional_reason(stmt: str, error: str) -> str | None:
    for key, (reason, pattern) in EDITION_DEPENDENT.items():
        if key in stmt and re.search(pattern, error, re.IGNORECASE):
            return reason
    return None


DB1 = "MDA_TEST_MAIN"
DB2 = "MDA_TEST_SECOND"
LOWPRIV_ROLE = "MDA_LOWPRIV"

STATEMENTS: list[str] = [
    f"CREATE DATABASE IF NOT EXISTS {DB1}",
    f"CREATE DATABASE IF NOT EXISTS {DB2}",
    f"CREATE SCHEMA IF NOT EXISTS {DB1}.SALES",
    f"CREATE SCHEMA IF NOT EXISTS {DB1}.ANALYTICS",
    # deliberately empty schema: exercises the empty-result ingestion path
    f"CREATE SCHEMA IF NOT EXISTS {DB1}.EMPTY_SCHEMA",
    f"CREATE SCHEMA IF NOT EXISTS {DB2}.STAGING",
    # plain table with a spread of types incl. all three timestamp flavors
    f"""
    CREATE OR REPLACE TABLE {DB1}.SALES.ORDERS (
        order_id     NUMBER(38,0) IDENTITY,
        customer     VARCHAR(200),
        amount       NUMBER(12,2),
        placed_ltz   TIMESTAMP_LTZ,
        placed_ntz   TIMESTAMP_NTZ,
        placed_tz    TIMESTAMP_TZ,
        attributes   VARIANT,
        tags         ARRAY,
        details      OBJECT,
        location     GEOGRAPHY
    )
    """,
    f"""
    INSERT INTO {DB1}.SALES.ORDERS
        (customer, amount, placed_ltz, placed_ntz, placed_tz, attributes, tags, details, location)
    SELECT
        'cust-' || seq4(),
        uniform(1, 1000, random()),
        current_timestamp(),
        current_timestamp()::timestamp_ntz,
        current_timestamp()::timestamp_tz,
        parse_json('{{"tier": "gold", "n": ' || seq4() || '}}'),
        array_construct('a', 'b'),
        object_construct('k', seq4()),
        to_geography('POINT(-122.35 37.55)')
    FROM table(generator(rowcount => 1000))
    """,
    # clustered table (clustering_key population in raw.tables)
    f"""
    CREATE OR REPLACE TABLE {DB1}.SALES.EVENTS (
        event_date DATE,
        event_type VARCHAR,
        payload    VARIANT
    ) CLUSTER BY (event_date)
    """,
    f"""
    INSERT INTO {DB1}.SALES.EVENTS
    SELECT dateadd(day, -uniform(0, 60, random()), current_date),
           'type-' || uniform(1, 5, random()),
           parse_json('{{"i": ' || seq4() || '}}')
    FROM table(generator(rowcount => 500))
    """,
    # transient table
    f"CREATE OR REPLACE TRANSIENT TABLE {DB1}.ANALYTICS.SCRATCH (id INT, note VARCHAR)",
    f"CREATE OR REPLACE TABLE {DB2}.STAGING.RAW_LOAD (id INT, loaded_at TIMESTAMP_LTZ)",
    # views: plain + secure, with dialect-interesting bodies
    f"""
    CREATE OR REPLACE VIEW {DB1}.ANALYTICS.ORDER_SUMMARY AS
    SELECT customer, count(*) AS n, sum(amount) AS total,
           attributes:tier::string AS tier
    FROM {DB1}.SALES.ORDERS
    GROUP BY customer, attributes:tier::string
    """,
    f"""
    CREATE OR REPLACE SECURE VIEW {DB1}.ANALYTICS.ORDER_SUMMARY_SECURE AS
    SELECT customer, sum(amount) AS total FROM {DB1}.SALES.ORDERS GROUP BY customer
    """,
    # UDFs: SQL and JavaScript (Python needs Anaconda-terms acceptance; skipped)
    f"""
    CREATE OR REPLACE FUNCTION {DB1}.SALES.NET_AMOUNT(amount NUMBER, fee NUMBER)
    RETURNS NUMBER AS 'amount - fee'
    """,
    f"""
    CREATE OR REPLACE FUNCTION {DB1}.SALES.JS_SCORE(a FLOAT, b FLOAT)
    RETURNS FLOAT LANGUAGE JAVASCRIPT
    AS 'if (A > B) {{ return A - B; }} return B - A;'
    """,
    # stored procedure (SQL scripting)
    f"""
    CREATE OR REPLACE PROCEDURE {DB1}.SALES.REFRESH_DEMO()
    RETURNS VARCHAR LANGUAGE SQL
    AS $$
    BEGIN
        RETURN 'refreshed';
    END
    $$
    """,
    # materialized view: Enterprise+ only; expected to fail on Standard trials
    f"""
    CREATE OR REPLACE MATERIALIZED VIEW {DB1}.ANALYTICS.ORDERS_MV AS
    SELECT customer, sum(amount) AS total FROM {DB1}.SALES.ORDERS GROUP BY customer
    """,
    # ── M2 feature objects: governance, pipeline, and platform signals ──
    # M3d fixtures: sequence, named file format, and a PK/FK constraint pair
    # (all lag-free via INFORMATION_SCHEMA)
    f"CREATE OR REPLACE SEQUENCE {DB1}.SALES.MDA_ORDER_SEQ START = 1 INCREMENT = 1",
    f"CREATE OR REPLACE FILE FORMAT {DB1}.SALES.MDA_CSV_FF TYPE = CSV SKIP_HEADER = 1",
    f"""
    CREATE OR REPLACE TABLE {DB1}.SALES.CUSTOMERS_DIM (
        customer_id NUMBER(38,0) PRIMARY KEY,
        name        VARCHAR(200)
    )
    """,
    f"""
    CREATE OR REPLACE TABLE {DB1}.SALES.ORDERS_FACT (
        order_id    NUMBER(38,0) PRIMARY KEY,
        customer_id NUMBER(38,0) REFERENCES {DB1}.SALES.CUSTOMERS_DIM(customer_id)
    )
    """,
    f"CREATE OR REPLACE STREAM {DB1}.SALES.ORDERS_STREAM ON TABLE {DB1}.SALES.ORDERS",
    f"CREATE OR REPLACE TABLE {DB1}.SALES.ORDERS_CLONE CLONE {DB1}.SALES.ORDERS",
    f"""
    CREATE OR REPLACE MASKING POLICY {DB1}.SALES.MASK_CUSTOMER AS (val VARCHAR)
    RETURNS VARCHAR ->
    CASE WHEN current_role() = 'ACCOUNTADMIN' THEN val ELSE '***' END
    """,
    # applied to the clone, not ORDERS: Snowflake forbids masking policies on
    # columns referenced by a materialized view (ORDERS_MV)
    f"""
    ALTER TABLE {DB1}.SALES.ORDERS_CLONE MODIFY COLUMN customer
    SET MASKING POLICY {DB1}.SALES.MASK_CUSTOMER
    """,
    f"""
    CREATE OR REPLACE ROW ACCESS POLICY {DB1}.SALES.RAP_EVENTS AS (etype VARCHAR)
    RETURNS BOOLEAN -> true
    """,
    f"""
    ALTER TABLE {DB1}.SALES.EVENTS
    ADD ROW ACCESS POLICY {DB1}.SALES.RAP_EVENTS ON (event_type)
    """,
    f"CREATE OR REPLACE TAG {DB1}.SALES.DATA_CLASS ALLOWED_VALUES 'public', 'pii'",
    f"ALTER TABLE {DB1}.SALES.ORDERS SET TAG {DB1}.SALES.DATA_CLASS = 'pii'",
    f"CREATE STAGE IF NOT EXISTS {DB1}.SALES.LOAD_STAGE",
    f"""
    CREATE OR REPLACE PIPE {DB1}.SALES.LOAD_PIPE AS
    COPY INTO {DB1}.ANALYTICS.SCRATCH FROM @{DB1}.SALES.LOAD_STAGE
    FILE_FORMAT = (TYPE = 'CSV')
    """,
    # ── M3a feature objects ─────────────────────────────────────────────
    f"""
    CREATE WAREHOUSE IF NOT EXISTS MDA_MULTI_WH
    WAREHOUSE_SIZE = 'XSMALL' MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = 2
    AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE
    """,
    # IF NOT EXISTS reports success without converging properties on an
    # existing warehouse — the ALTER makes the multi-cluster fixture real
    f"ALTER WAREHOUSE MDA_MULTI_WH SET MIN_CLUSTER_COUNT = 1 MAX_CLUSTER_COUNT = 2",
    f"""
    CREATE OR REPLACE TABLE {DB1}.ANALYTICS.EMBEDDINGS (
        id INT, emb VECTOR(FLOAT, 768)
    )
    """,
    f"ALTER TABLE {DB1}.SALES.EVENTS ADD SEARCH OPTIMIZATION",
    # public tutorial bucket; a failure here means the bucket or network
    # path changed and the fixture needs a new source — required on purpose
    f"""
    CREATE STAGE IF NOT EXISTS {DB1}.ANALYTICS.EXT_STAGE
    URL = 's3://ocient-examples/metabase_samples/parquet/'
    """,
    f"""
    CREATE OR REPLACE EXTERNAL TABLE {DB1}.ANALYTICS.EXT_TIPS
    LOCATION = @{DB1}.ANALYTICS.EXT_STAGE
    PATTERN = '.*tips.parquet'
    FILE_FORMAT = (TYPE = PARQUET)
    AUTO_REFRESH = FALSE
    """,
    f"CREATE SHARE IF NOT EXISTS MDA_TEST_SHARE",
    f"GRANT USAGE ON DATABASE {DB1} TO SHARE MDA_TEST_SHARE",
    f"GRANT USAGE ON SCHEMA {DB1}.ANALYTICS TO SHARE MDA_TEST_SHARE",
    f"GRANT SELECT ON VIEW {DB1}.ANALYTICS.ORDER_SUMMARY_SECURE TO SHARE MDA_TEST_SHARE",
    # low-privilege role for fallback / least-privilege testing: sees DB1 only
    f"CREATE ROLE IF NOT EXISTS {LOWPRIV_ROLE}",
    f"GRANT USAGE ON DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT USAGE ON ALL SCHEMAS IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT SELECT ON ALL TABLES IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT SELECT ON ALL VIEWS IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
]


def _run_all(cur, statements, required_failures, optional_failures) -> None:
    for stmt in statements:
        label = " ".join(stmt.split())[:80]
        try:
            cur.execute(stmt)
            print(f"ok    {label}")
        except Exception as exc:  # noqa: BLE001
            err = str(exc).splitlines()[0]
            reason = _optional_reason(stmt, err)
            if reason:
                optional_failures.append((label, err, reason))
                print(f"skip  {label}")
            else:
                required_failures.append((label, err))
                print(f"FAIL  {label}")


def main() -> int:
    import snowflake.connector

    cfg = SnowflakeConfig.from_env()
    conn = snowflake.connector.connect(**cfg.connect_kwargs())
    cleanups: list = []
    required_failures: list[tuple[str, str]] = []
    optional_failures: list[tuple[str, str, str]] = []
    try:
        cur = conn.cursor()
        user = cur.execute("SELECT current_user()").fetchone()[0]
        warehouse = cfg.warehouse
        statements = _build_statements(user, warehouse, cleanups)
        _run_all(cur, statements, required_failures, optional_failures)
    finally:
        try:
            for cleanup in cleanups:
                try:
                    cleanup()
                except Exception as exc:  # noqa: BLE001
                    print(f"cleanup failed (continuing): {exc}")
        finally:
            conn.close()

    ok = len(statements) - len(required_failures) - len(optional_failures)
    print(f"\n{ok}/{len(statements)} statements succeeded")
    for label, err, reason in optional_failures:
        print(f"  optional ({reason}): {label}\n    -> {err}")
    for label, err in required_failures:
        print(f"  REQUIRED FAILURE: {label}\n    -> {err}")
    print(
        "\nINFORMATION_SCHEMA sees these objects immediately (lite profile).\n"
        "ACCOUNT_USAGE lags ~45min-2h; run the standard profile after that."
    )
    return 1 if required_failures else 0


def _build_statements(user: str, warehouse: str | None, cleanups: list) -> list[str]:
    statements = list(STATEMENTS)
    statements.append(f'GRANT ROLE {LOWPRIV_ROLE} TO USER "{user}"')
    if warehouse:
        statements.append(f"GRANT USAGE ON WAREHOUSE {warehouse} TO ROLE {LOWPRIV_ROLE}")
        # warehouse-dependent M2 objects
        statements.append(f"""
            CREATE OR REPLACE DYNAMIC TABLE {DB1}.ANALYTICS.ORDERS_DYNAMIC
            TARGET_LAG = '1 hour' WAREHOUSE = {warehouse} AS
            SELECT customer, sum(amount) AS total FROM {DB1}.SALES.ORDERS GROUP BY customer
        """)
        statements.append(f"""
            CREATE OR REPLACE TASK {DB1}.SALES.DAILY_ROLLUP
            WAREHOUSE = {warehouse} SCHEDULE = '1440 MINUTE' AS
            SELECT count(*) FROM {DB1}.SALES.ORDERS
        """)
        # stage a real main file first: Streamlit requires MAIN_FILE to
        # exist in ROOT_LOCATION, so the fixture must be deterministic
        import pathlib
        import tempfile

        tmpdir = tempfile.TemporaryDirectory()
        cleanups.append(tmpdir.cleanup)
        app = pathlib.Path(tmpdir.name) / "app.py"
        app.write_text("import streamlit as st\nst.title('MDA seed fixture')\n")
        statements.append(
            f"PUT file://{app} @{DB1}.SALES.LOAD_STAGE "
            "AUTO_COMPRESS = FALSE OVERWRITE = TRUE"
        )
        statements.append(f"""
            CREATE STREAMLIT IF NOT EXISTS {DB1}.ANALYTICS.SALES_APP
            ROOT_LOCATION = '@{DB1}.SALES.LOAD_STAGE'
            MAIN_FILE = 'app.py'
            QUERY_WAREHOUSE = {warehouse}
        """)

    return statements


if __name__ == "__main__":
    sys.exit(main())
