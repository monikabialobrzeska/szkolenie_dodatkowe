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

## Explicitly out of scope for this flow

Databricks adapter and parity test (Stage 2), `public_api`, model reading pilot (Stage 3), verdict recording UI, OSM vs Overture check, tertiary roads in `missing_limit`.
