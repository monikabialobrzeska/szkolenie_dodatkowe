---
name: check-databricks
description: Use when the user asks to check, verify or troubleshoot access to the course Databricks workspace (workspace.map_qa tables), e.g. "sprawdź databricks", after setting DATABRICKS_* variables, or before Stage 2 work. Read-only; one SELECT; never reveals secrets.
---

# Check Databricks access (map-qa)

Verifies that this agent session can read the five shared tables `workspace.map_qa.*` and that their row counts equal the local snapshot.

## Hard rules

- Check only the **presence** of variables, never their values: `[ -n "${!v}" ]` in bash. Never `echo $DATABRICKS_TOKEN`, never `env | grep`, never `databricks auth env`, never `databricks current-user me` (identity data).
- Never ask the user to paste the token into the conversation or into `!` commands; never write it to a file.
- Only `SELECT`. Never create, modify or delete tables, schemas or other resources, even though the token technically could.
- One statement. If it is `PENDING`/`RUNNING`, poll the **same** `statement_id` (the script does this); never re-submit. `FAILED`/`CANCELED` is an error, never an empty result.
- Do not run `databricks aitools install` or other configuration-changing commands without asking.

## Steps

1. **Presence and format** (bash):
   for `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` print `set` / `MISSING`; check that the host starts with `https://` and has no trailing `/` (print only `ok`/`problem`).
2. **Tools:** `databricks version`, `uv --version`. If `databricks` or `uv` is missing, give the install commands for the user to run with `!`:
   `winget install Databricks.DatabricksCLI --accept-source-agreements --accept-package-agreements` and `winget install astral-sh.uv --accept-source-agreements --accept-package-agreements`; PATH changes need a new terminal.
3. **If any variable is MISSING**, stop and give the user these steps (they run them themselves, outside the conversation):
   1. `/exit`
   2. In a **new PowerShell 7** window, in the repository root:
      ```powershell
      $env:DATABRICKS_HOST = "<host from the trainer, https://..., no trailing />"
      $env:DATABRICKS_TOKEN = Read-Host "Token Databricks" -MaskInput
      $env:DATABRICKS_SQL_WAREHOUSE_ID = "97c455ee6fb1fcb9"
      claude --continue
      ```
      (warehouse ID from `course-materials/exercise-data/map-qa/DATABRICKS.md`; variables live only in that window.)
   3. Ask again: "sprawdź databricks".
4. **Help first:** `uv run --python 3.12 course-materials/exercise-data/map-qa/query_databricks.py --help` (set `PYTHONIOENCODING=utf-8` to avoid garbled Polish text).
5. **One read:**
   `uv run --python 3.12 course-materials/exercise-data/map-qa/query_databricks.py --statement "SELECT 'osm_roads' AS source, count(*) AS rows FROM workspace.map_qa.osm_roads UNION ALL SELECT 'overture_segments', count(*) FROM workspace.map_qa.overture_segments UNION ALL SELECT 'photos', count(*) FROM workspace.map_qa.photos UNION ALL SELECT 'photo_road_links', count(*) FROM workspace.map_qa.photo_road_links UNION ALL SELECT 'traffic_signs', count(*) FROM workspace.map_qa.traffic_signs"`
   Show only `state`, the rows and `truncated`. The first call may wake a stopped warehouse; waiting is not an error.
6. **Compare** with the local snapshot (`sources.json` / `reports/map-qa/profile.md`): OSM 99,768 · Overture 85,405 · photos 561 · photo_road_links 561 · traffic_signs 1,730.
7. **Report** a table (table, Databricks, local, match), tool versions, UTC time of the read, and remind:
   - counts arrive as JSON strings (cast by `schema` in any adapter),
   - geometry in Databricks is `BINARY` WKB,
   - the variables exist only in the PowerShell window that started this session.

## Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| HTTP 401/403 | wrong or expired token, or token without `sql` scope | user re-enters the token in a new window; never paste it here |
| `FAILED` with table not found | wrong catalog/schema or warehouse | check `DATABRICKS.md`; ask the trainer |
| long `PENDING` | warehouse starting | wait; the script polls the same statement |
| `databricks: command not found` | PATH not refreshed | new terminal, or sign out/in of Windows |
