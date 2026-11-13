# app/src/mapqa/sources/ — agent instructions (scoped)

Source adapters (`files`, `databricks`). Spec: ADR-001 (contract, config), ADR-004 (Databricks). Root and `app/AGENTS.md` still apply.

- **Parity is structural:** every adapter ends in `normalise.py`. Change dtypes there only, never per adapter; the parity test (`tests/integration/test_databricks.py`) requires byte-identical `review_queue.*` from both sources.
- **Secrets:** the token is read only from `DATABRICKS_TOKEN` and only sent in the `Authorization` header. Never print, log, store or put it in an exception message; check variables by presence and shape only.
- **Read-only:** add statements only as fixed `SELECT` / `DESCRIBE` templates in `databricks.py`; the client rejects anything else. No retries, no re-submit, no fallback to files.
- **Mocks come from captures:** `tests/fixtures/databricks/capture.py` records real responses (redacts e-mails and `DESCRIBE HISTORY` user columns, checks the token is absent). Add a capture before writing a test for a new response shape.
- **Databricks tests** are marked `databricks` and skip with a reason when `DATABRICKS_HOST` / `DATABRICKS_TOKEN` / `DATABRICKS_SQL_WAREHOUSE_ID` are missing. The session must be started from the PowerShell window where they are set (`.agents/skills/check-databricks`).
- **Size limit:** `INLINE` results are limited to 25 MiB; `osm_roads` is read in `ROAD_PARTITIONS` partitions. Re-measure with `capture.py` if the data grows (ADR-004 §6 review trigger).
