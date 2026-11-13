# Implementation Plan — Stage 1 (done) and Stage 2 (Databricks)

**Branch:** `map-app-implementation` · **Updated:** 2026-11-13

This plan only orders the work and tracks status. What to build lives in `docs/PRD.md` (scope by stage: §6 table) and `docs/mvp-scope.md` §2; how to build it lives in `docs/ADR/`; measured counts live in `reports/map-qa/profile.md`. Working rules (TDD, uv, read-only inputs, `--help` first, commit format, **push only when the user asks**) are in `AGENTS.md` and `app/AGENTS.md` — they are not repeated here.

Each phase ends green with one focused commit; an agent running the plan without stopping does the phases top to bottom and checks each exit criterion before moving on.

## Stage 1 — local files + read-only viewer (done)

| Phase | Result | Commit |
|---|---|---|
| P0 Scope & docs | viewer scoped (ADR-003) | `e240dbc` |
| P1 Rule core | interpretation, 4 checks, ranking/IDs, real-data fixtures (ADR-002) | `1a73561` |
| P2–P4 Source, pipeline, CLI, viewer | `run`, `check-source`, `view` (ADR-000/001/003) | `8544b06` |
| P5–P6 Verification + manual QA | 55 tests green, full run 52 s, Edge screenshots in `reports/map-qa/qa/` | `bd7ed13`, `85560da` |
| P7–P8 Docs + wrap-up | `app/README.md`, `app/AGENTS.md`, example config | `e35863a` |

Definition of done for Stage 1 = ADR-000 §10 and ADR-003 §8 technical acceptance criteria; all met.

## Stage 2 — Databricks source

**Goal:** PRD AC-05a–AC-05f, `mvp-scope.md` §2 items 9–10: `mapqa run --source databricks` reads the five `workspace.map_qa` tables read-only and produces a review queue byte-identical to `--source files` on the pinned snapshot.

**Prerequisite:** the session is started from a PowerShell window with `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` set; verify by following `.agents/skills/check-databricks/SKILL.md` (`/check-databricks` once its `.claude/skills` junction exists). Without access the agent stops and asks; it never mocks from documentation.

| # | Phase | Delivers | Exit criterion | Status |
|---|---|---|---|---|
| S2-0 | Access check | variables present, token shape ok, one `SELECT count(*)` | 5/5 counts equal files | ✅ 2026-11-13 |
| S2-1 | Discovery + real captures | `DESCRIBE` of the 5 tables; verbatim captures (per `AGENTS.md` mock rule) of a small `SELECT`, a multi-chunk result, a `FAILED` statement and a poll sequence in `app/tests/fixtures/databricks/`; measured size of the projected `osm_roads` result | captures contain no secret (token-sentinel grep); transport limits measured | |
| S2-2 | ADR-004 Databricks adapter | decisions listed below, based on S2-1 | ADR accepted; open trade-offs confirmed with the user | |
| S2-3 | TDD: REST client | client per ADR-004, unit tests driven by the S2-1 captures | red → green, no network in unit tests | |
| S2-4 | TDD: `DatabricksSource` | same `InputBundle` contract as files (ADR-001 §4.2), `check-source --source databricks` | unit tests green; existing suite green | |
| S2-5 | Parity + integration | tests marked `databricks` (register the marker in `app/pyproject.toml`; skip with an explicit reason when variables are missing): counts = files, `review_queue.*` byte-identical, wrong token → exit 4 without leaking it, no fallback | parity green against the real warehouse | |
| S2-6 | Manual QA | real runs on both sources, `diff`, timing incl. warehouse wake-up, viewer from the Databricks run, screenshots in `reports/map-qa/qa/stage2/` | evidence recorded | |
| S2-7 | Docs | `app/README.md`, `mapqa.example.toml`, `app/AGENTS.md` (or `app/src/mapqa/sources/AGENTS.md`), skill notes | docs match `--help` | |

**ADR-004 must decide** (inputs: ADR-001 §4.5, S2-1 measurements, consistency review 2026-11-13):
1. Transport for ~100k rows with geometry (INLINE + chunks, `osm_id` range paging, or `EXTERNAL_LINKS`/Arrow), server-side projection of `maxspeed*` tags, geometry encoding.
2. Type mapping from `manifest.schema` (values arrive as JSON strings), polling and timeouts, error → exit 4 mapping, read-only guard (fixed `SELECT` templates only).
3. Snapshot identity for a remote source (`sources.json` is a local file) and what `run_summary.json` `inputs.*.sha256_*` hold for remote inputs (TAC-04).
4. Viewer in a Databricks run: road geometries from the active adapter's `InputBundle`, not from local `osm_roads.parquet`; whether offline thumbnails are an allowed local presentation asset (otherwise PRD §7 "mixing sources" is violated).
5. Keep the connection-settings order fixed in ADR-001 §3.3 (TOML, then `DATABRICKS_*` fallback; token only from the environment).

## Out of scope

Stage 1 and Stage 2: `public_api`, writing to Databricks, Databricks MCP, mixing sources in one run, automatic fallback, verdict recording, OSM vs Overture check, model reading pilot (Stage 3), tertiary roads in `missing_limit`.
