#!/bin/zsh
# Upload the md-assess wheel to the MotherDuck Demo org (md_assess_dist.main.wheels)
# so the Databricks assessment Flight can load it. Prompts for the Demo-org token
# (not echoed, not saved). No Databricks credentials involved.
unset DATABRICKS_TOKEN DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET DATABRICKS_HOST DATABRICKS_WAREHOUSE_ID motherduck_token
cd "$(dirname "$0")/../.." || exit 1
DIST=/tmp/md-assess-dist
rm -rf "$DIST" && uv build --wheel -o "$DIST" >/dev/null || exit 1
WHL=$(ls "$DIST"/*.whl)
print -n "Paste MotherDuck Demo-org token (hidden): "
read -rs MOTHERDUCK_TOKEN
echo
export MOTHERDUCK_TOKEN
.venv/bin/python - "$WHL" <<'EOF'
import hashlib, sys, pathlib, duckdb
p = pathlib.Path(sys.argv[1]); data = p.read_bytes()
ver = p.name.split("-")[1]; sha = hashlib.sha256(data).hexdigest()
con = duckdb.connect("md:")
con.execute("CREATE DATABASE IF NOT EXISTS md_assess_dist")
con.execute("CREATE TABLE IF NOT EXISTS md_assess_dist.main.wheels "
            "(version VARCHAR, filename VARCHAR, sha256 VARCHAR, content BLOB, uploaded_at TIMESTAMPTZ)")
con.execute("INSERT INTO md_assess_dist.main.wheels VALUES (?, ?, ?, ?, now())",
            [ver, p.name, sha, data])
print(f"uploaded {p.name} ({len(data):,} bytes, sha256 {sha[:12]}) to md_assess_dist.main.wheels")
EOF
unset MOTHERDUCK_TOKEN
