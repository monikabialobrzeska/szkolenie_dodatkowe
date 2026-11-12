# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb==1.5.6"]
# ///
"""Run each statement of a SQL file read-only against files in a data folder."""
import os
import sys
import duckdb

sql_path, data_dir = sys.argv[1], sys.argv[2]
sql = open(sql_path, encoding="utf-8").read()
os.chdir(data_dir)
con = duckdb.connect()
con.execute("SET memory_limit='3GB'; SET threads=4")
print("duckdb", duckdb.__version__)
for st in con.extract_statements(sql):
    q = st.query.strip()
    print("\n>>>", q[:160].replace("\n", " "), flush=True)
    try:
        r = con.sql(q)
        if r is not None:
            r.show(max_rows=60, max_width=250)
    except Exception as e:  # report and continue
        print("ERROR:", type(e).__name__, str(e)[:400])
