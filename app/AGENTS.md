# app/ — agent instructions (scoped)

Applies to work inside `app/`. Root `AGENTS.md` still applies; this file adds stack-specific rules.

## Stack and commands (run from `app/`)

- Python 3.12 via uv only: `uv sync`, `uv run mapqa …`, `uv run pytest`. Never use the system Python (3.14, no deps).
- Unit tests: `uv run pytest -m "not integration"` (~5 s). Full suite incl. real CLI: `uv run pytest` (~4.5 min with Databricks; the `databricks` tests skip with a reason when its variables are missing). Without the warehouse: `uv run pytest -m "not databricks"`.
- Manual QA: `uv run mapqa run` then `uv run mapqa view`; for screenshots drive system Edge with Playwright for Python (`channel="msedge"`, no browser download): `uv run --with playwright==1.55.0 python <qa-script>`; save to `reports/map-qa/qa/`.

## Where things live

- Rules and reason codes: `src/mapqa/interpret.py`, `src/mapqa/checks/*` — the spec is `docs/ADR/002-detection-rules.md`; change the ADR and tests first.
- Source reading: `src/mapqa/sources/` — `files.py`, `databricks.py` (+ `databricks_client.py`), shared `normalise.py`. Spec: ADR-001, ADR-004; scoped rules in `src/mapqa/sources/AGENTS.md`.
- Outputs and viewer: `src/mapqa/outputs/` (viewer template is plain HTML/JS + Leaflet 1.9.4). Spec: ADR-000 §6.2, ADR-003.

## Rules

- Inputs in `course-materials/exercise-data/map-qa/` are read-only; tests write only to `tmp_path`.
- Fixtures are real rows: extend `tests/fixtures/build_fixtures.py`, regenerate, and record new cases in `tests/fixtures/README.md`. Mark synthetic values `# synthetic` in test code.
- Keep outputs deterministic: sort explicitly, round at write time, no run timestamps in `review_queue.*`.
- Comparisons that may meet NULL must be NULL-safe (bug seen during profiling).
- UI text is English and says "candidate", never "error".
- Expected counts on the pinned snapshot are asserted in `tests/integration/test_cli.py`; if a change moves them out of range, investigate before touching the range.
