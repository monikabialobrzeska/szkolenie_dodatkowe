# Implementation Plan — Stage 1 + Viewer (one continuous flow)

**Date:** 2026-11-12 · **Branch:** `map-app-implementation` · **Specs:** `docs/PRD.md`, `docs/mvp-scope.md`, `docs/ADR/000–003`, `reports/map-qa/profile.md`

Phases are ordered by dependency: each phase only uses what earlier phases delivered, and each ends green (tests pass) with one focused commit. An agent running this plan without stopping executes the phases top to bottom, checks the exit criteria, and only then moves on. Out-of-scope items are listed at the end and must not be started.

## Ground rules for the whole flow

- TDD: tests first, run them red for the expected reason, implement, run green (`AGENTS.md`).
- Run everything from `app/` with `uv run …`; never the system Python.
- Inputs in `course-materials/exercise-data/map-qa/` are read-only; outputs go to `reports/map-qa/stage1/` (git-ignored) or `tmp_path` in tests.
- Look up CLI/library details with `--help` / installed version before use.
- No paid APIs, no Databricks calls in Stage 1, no secrets in files or output.
- Commit format `Area: summary` + the attribution line; push to `mine` only at the end of a phase group (P3, P6, P8).

## Phases

| # | Phase | Priority | Depends on | Delivers | Exit criteria | Status |
|---|---|---|---|---|---|---|
| P0 | Scope & docs | must | — | PRD/ADR updates, ADR-003 viewer, this plan | docs committed | ✅ `e240dbc` |
| P1 | Scaffold + rule core | must | P0 | uv project `app/`, real-data fixtures, `interpret`, 4 checks, ranking/IDs | 6 ADR-002 unit cases green | ✅ `1a73561` |
| P2 | Config + files adapter | must | P1 | `config.py` (TOML, precedence, strict/secret keys), `sources/` (`FilesSource`: check, checksums, load with `ST_AsWKB`, timestamps, bbox) | `check-source` works on the snapshot; config error tests green | ✅ `8544b06` (with P3–P4: pipeline imports the viewer) |
| P3 | Pipeline + CLI `run` | must | P2 | `evidence.py`, `pipeline.py`, `outputs/` (CSV, GeoJSON, `summary.md`, `run_summary.json`, atomic write, `last_failed_run.json`), `cli.py` (`run`, `check-source`, exit codes 0–4), `__main__.py` | integration tests green: counts, determinism, input checksums, missing input → 3, Stage 2 sources → 4; TAC-002-04 ranges | ✅ `8544b06` |
| P4 | Viewer | must | P3 | `outputs/viewer.py` + template (Leaflet 1.9.4, embedded JSON, filters, detail, photos, attribution), `mapqa view`, `run` writes `viewer.html` | unit render test + view integration tests green | ✅ `8544b06` |
| P5 | Full verification | must | P4 | full `uv run pytest`, timing of a full run, warnings fixed | all tests green, no new warnings, full run < 3 min (TAC-02) | ✅ 55 passed, run 52 s |
| P6 | Manual QA | must | P5 | real `mapqa run` + `mapqa view`; Playwright (msedge) screenshots: initial page, filtered table, empty filter, candidate detail; console errors checked; spot-check 5 candidates against data | screenshots in `reports/map-qa/qa/`, no console errors, UI English, attribution visible | ✅ `bd7ed13` (found + fixed: sign detail lacked the compared OSM way) |
| P7 | Project docs | should | P6 | `app/README.md` (install, commands, outputs, exit codes), `app/mapqa.example.toml`, scoped `app/AGENTS.md`, ADR notes for any deviation found during implementation | docs consistent with behaviour (`--help` matches README) | ✅ |
| P8 | Wrap-up | must | P7 | final commit(s), push to `mine/map-app-implementation`, summary with QA evidence | branch pushed, working tree clean except `.env.example` | ✅ |

### Priority order if time runs short

P3 → P4 → P5 → P6 are the product; P7 can be reduced to `app/README.md` + example config. Never skip P5/P6: a task is not done without verification and manual QA.

## Definition of done (whole flow)

- `uv run mapqa run` on the pinned snapshot exits 0 and writes `review_queue.csv`, `review_queue.geojson`, `summary.md`, `run_summary.json`, `viewer.html`.
- `uv run mapqa view` opens the viewer; filters, map selection, detail panel and photo strip work; attribution visible.
- All PRD Stage 1 ACs in scope and TACs of ADR-000…003 pass or are explicitly reported as not met.
- Tests green, two runs byte-identical, inputs unchanged.

---

# Stage 2 — Databricks source (planned 2026-11-13)

**Goal (`mvp-scope.md` §2 items 9–10, PRD AC-05a–AC-05f):** `mapqa run --source databricks` reads the five `workspace.map_qa` tables read-only and produces a review queue **byte-identical** to `--source files` on the same pinned snapshot.

**Hard prerequisite:** the Claude Code session must be started from a PowerShell window where `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` are set (see `.agents/skills/check-databricks`). Without them S2-1, S2-5 and S2-6 cannot run; the agent stops and asks instead of mocking from documentation.

| # | Phase | Priority | Depends on | Delivers | Exit criteria | Status |
|---|---|---|---|---|---|---|
| S2-0 | Access check | must | — | `/check-databricks`: variables present, one `SELECT count(*)`, counts equal files | 5/5 counts match | |
| S2-1 | Discovery + real captures | must | S2-0 | `DESCRIBE` of the 5 tables (types vs local files); verbatim captures (status, headers without secrets, body, date, statement, warehouse id) of: a small `SELECT`, a multi-chunk result, a `FAILED` statement, a `PENDING` → poll sequence → `app/tests/fixtures/databricks/`; measured size of the projected `osm_roads` result | captures saved with no secrets (grep for the token sentinel); transport limits measured | |
| S2-2 | ADR-004 Databricks adapter | must | S2-1 | decisions: transport (INLINE + chunks vs `osm_id` range paging vs `EXTERNAL_LINKS`/Arrow, from the measurement), server-side projection (`get_json_object` for `maxspeed*` tags, `hex(geometry)` or base64), type mapping from `manifest.schema`, polling/timeouts, error → exit 4 mapping, read-only guard (fixed `SELECT` templates only), config/env precedence for host/warehouse/schema | ADR reviewed with the user if a transport trade-off remains | |
| S2-3 | TDD: REST client + type casting | must | S2-2 | `sources/databricks_client.py` (urllib, no new dependency): submit, poll same `statement_id`, follow chunks, `FAILED`/`CANCELED`/HTTP 401/403 → `SourceUnavailable`, never prints the token; unit tests driven by the S2-1 captures | red → green; no test needs network | |
| S2-4 | TDD: `DatabricksSource` + shared normalisation | must | S2-3 | adapter returning the same `InputBundle` (columns, dtypes, sort order, EPSG:2180) as `FilesSource`; refactor a shared `normalise_*` step so parity is structural, not coincidental; `check-source --source databricks` (reachability + credentials present) | unit tests green; existing 55 tests still green | |
| S2-5 | Parity + integration | must | S2-4 | integration tests marked `databricks` (skipped with an explicit reason when variables are missing): `check-source` exit 0, row counts = files, **`review_queue.csv`/`.geojson` byte-identical to files**, wrong token → exit 4 without leaking it, no fallback to files | parity test green against the real warehouse | |
| S2-6 | Manual QA | must | S2-5 | real `mapqa run --source databricks`, timing incl. warehouse wake-up, `diff` with the files run, `run_summary.json` shows source/location/read time (AC-05f), viewer opens from the Databricks run, screenshots | evidence in `reports/map-qa/qa/stage2/` | |
| S2-7 | Docs + wrap-up | should | S2-6 | `app/README.md`, `mapqa.example.toml`, `app/AGENTS.md`, `check-databricks` skill notes, ADR-001 §4.5 status, commits | docs match `--help`; branch pushed when the user asks | |

**Stage 2 out of scope:** `public_api`, writing anything to Databricks, Databricks MCP, mixing sources in one run, automatic fallback.

## Explicitly out of scope for this flow

Databricks adapter and parity test (Stage 2), `public_api`, model reading pilot (Stage 3), verdict recording UI, OSM vs Overture check, tertiary roads in `missing_limit`.
