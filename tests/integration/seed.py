"""Seed a Snowflake test account with objects covering every M1 extractor.

Usage (with SNOWFLAKE_* env vars set, role must be able to create databases —
ACCOUNTADMIN on a trial):

    set -a; source .env.snowflake; set +a
    .venv/bin/python tests/integration/seed.py

Statements are a Python list (not a .sql file) because UDF/procedure bodies
contain semicolons and $$-quoting that naive statement splitting mangles.
Each statement runs independently; failures are collected and reported, not
fatal — e.g. materialized views fail on Standard-edition trials, which is fine.

Note: ACCOUNT_USAGE views lag by ~45min-2h. INFORMATION_SCHEMA (lite profile)
sees these objects immediately; run the standard profile after the lag.
"""

from __future__ import annotations

import sys

from md_migration_assessment.collect.snowflake import SnowflakeConfig

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
    # low-privilege role for fallback / least-privilege testing: sees DB1 only
    f"CREATE ROLE IF NOT EXISTS {LOWPRIV_ROLE}",
    f"GRANT USAGE ON DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT USAGE ON ALL SCHEMAS IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT SELECT ON ALL TABLES IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
    f"GRANT SELECT ON ALL VIEWS IN DATABASE {DB1} TO ROLE {LOWPRIV_ROLE}",
]


def main() -> int:
    import snowflake.connector

    cfg = SnowflakeConfig.from_env()
    conn = snowflake.connector.connect(**cfg.connect_kwargs())
    cur = conn.cursor()

    user = cur.execute("SELECT current_user()").fetchone()[0]
    warehouse = cfg.warehouse
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

    failures: list[tuple[str, str]] = []
    for stmt in statements:
        label = " ".join(stmt.split())[:80]
        try:
            cur.execute(stmt)
            print(f"ok    {label}")
        except Exception as exc:  # noqa: BLE001
            failures.append((label, str(exc).splitlines()[0]))
            print(f"FAIL  {label}")

    conn.close()
    print(f"\n{len(statements) - len(failures)}/{len(statements)} statements succeeded")
    if failures:
        print("\nFailures (materialized view is expected to fail on Standard edition):")
        for label, err in failures:
            print(f"  {label}\n    -> {err}")
    print(
        "\nINFORMATION_SCHEMA sees these objects immediately (lite profile).\n"
        "ACCOUNT_USAGE lags ~45min-2h; run the standard profile after that."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
