---
name: profile-data
description: Use when the user asks to profile the map-qa input data (pinned or refreshed snapshot), check data quality, counts, CRS, geometry validity, duplicates, key relations or maxspeed formats, compare local files with Databricks, or verify whether PRD/ADR assumptions still match the data. Read-only; writes only a Markdown report.
---

# Profile map-qa data

Produces `reports/map-qa/profile.md` (Polish) with facts that change the design and questions for a human. Proven on the pinned Kraków snapshot (2026-11-12).

## Hard rules

- **Read-only.** Never modify, move or re-save anything in the data folder. Prove it with SHA-256 before and after.
- **No paid APIs.** Only local DuckDB and, optionally, one Databricks `SELECT count(*)` statement.
- **Secrets:** check only the *presence* of `DATABRICKS_*` variables, never print values. Never run `databricks auth env` or `databricks current-user me`. Never ask the user to paste a token.
- **Script name** is `profile_data.py` (not `profiledata.py`).
- **NULL-safe comparisons:** wrap every comparison that may meet NULL in `coalesce(..., false)`. A bare `NOT (a = v OR b = v)` silently drops rows. Sanity-check counts against `docs/mvp-scope.md`; a large deviation means a query bug first.
- Run commands from the repository root. Data folder default: `course-materials/exercise-data/map-qa` (the user may name another, e.g. a refreshed snapshot).

## Steps

1. **Read** `<data>/README.md` and `<data>/profile.sql`. Skim `docs/mvp-scope.md` §1 and `docs/ADR/` if they exist, so you know which assumptions to test.
2. **Versions:** `uv --version`; Python and DuckDB versions come from the script output.
3. **Checksums before** (to a scratchpad file, not the repo):
   `sha256sum osm_roads.parquet overture_segments.parquet photos.csv photo_road_links.csv traffic_signs.geojson sources.json` inside `<data>`.
4. **Bundled profile:** `uv run --python 3.12 <data>/profile_data.py` (set `PYTHONIOENCODING=utf-8`). First run installs the DuckDB `spatial` extension into the user profile.
5. **Extra profile:** `uv run --python 3.12 .agents/skills/profile-data/scripts/run_sql.py .agents/skills/profile-data/scripts/profile_extra.sql <data>`
   Covers schemas, GeoParquet CRS metadata, geometry types/empty/NULL, bbox containment, duplicates, `photo_id` key relations, link flags, all `maxspeed*` keys and formats, `source:maxspeed` vs value, conditional/variable/directional, bridges/tunnels, missing values, sign dates. Output is long: redirect to a scratchpad file and read it.
6. **Rule impact** (if `docs/ADR/002-detection-rules.md` exists): `run_sql.py .../rule_impact.sql <data>` — measured counts for `tag_inconsistency`, `stale_sign`, `bridge_or_tunnel`, single-road signs. If ADR-002 rules changed, update the SQL first.
7. **Databricks (optional):** if `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` are all present, run once:
   `uv run --python 3.12 <data>/query_databricks.py --statement "SELECT 'osm_roads' AS source, count(*) AS rows FROM workspace.map_qa.osm_roads UNION ALL SELECT 'overture_segments', count(*) FROM workspace.map_qa.overture_segments UNION ALL SELECT 'photos', count(*) FROM workspace.map_qa.photos UNION ALL SELECT 'photo_road_links', count(*) FROM workspace.map_qa.photo_road_links UNION ALL SELECT 'traffic_signs', count(*) FROM workspace.map_qa.traffic_signs"`
   Record UTC time and state. Values arrive as JSON strings. If variables are missing, say so and skip (see the `check-databricks` skill).
8. **Checksums after** and `diff` with step 3. Any difference: stop and report.
9. **Write the report** `reports/map-qa/profile.md` (ask before overwriting an existing one; offer `profile-<YYYY-MM-DD>.md`). Sections:
   1. Polecenia i wersje
   2. Liczności i porównanie lokalne vs Databricks vs `sources.json`
   3. Typy kolumn
   4. CRS i geometria
   5. Duplikaty
   6. Relacje kluczy `photo_id` (+ OSM/Overture ids)
   7. `maxspeed`: rozkład i formaty (liczby, jednostki, `PL:*`, `none`, wiele wartości, kierunkowe, warunkowe, zmienne, `source:maxspeed`)
   8. Braki
   9. Integralność wejść (SHA-256)
   10. Pięć faktów, które zmieniają projekt — each tied to a concrete PRD/ADR section
   11. Trzy pytania do człowieka — each with numbers and options
10. **Report back** in chat: what ran, counts match or not, the 5 facts, the 3 questions, and which PRD/ADR sections are now outdated. Do not edit PRD/ADR without the user's answers; do not commit unless asked.

## Known facts of the pinned snapshot (for quick sanity checks)

OSM 99,768 · Overture 85,405 · photos 561 · links 561 · signs 1,730 · main roads without any limit 290 · plain `maxspeed` always an integer · DuckDB 1.5.6 returns `GEOMETRY`, not WKB · Overture GeoParquet has no `crs` (= OGC:CRS84) · 230 OSM ways have photos · `tunnel=building_passage` 605.
