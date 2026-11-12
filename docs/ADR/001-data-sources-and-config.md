# ADR-001: Data Sources and Configuration

**Date:** 2026-11-12
**Status:** Accepted (revised 2026-11-12 after the data profile `reports/map-qa/profile.md` and a verified Databricks read)
**Relates to:** [`docs/ADR/000-main-architecture.md`](000-main-architecture.md), PRD AC-01, AC-02, AC-04, AC-05, AC-05a–AC-05f, `mvp-scope.md` §3

**Revision 2026-11-12:** geometry handling decided from the measured DuckDB 1.5.6 behaviour (§4.4); Overture CRS made explicit; verified Databricks facts recorded for Stage 2 (§4.5).

---

## 1. Scope

Covers: the configuration file and its precedence rules, source selection, the source adapter interface, the Stage 1 `files` adapter (reading, column normalisation, reprojection, validation, checksums) and the `check-source` command.

Does not cover: the `databricks` adapter (Stage 2) and the `public_api` adapter (deferred) beyond their rejection in Stage 1; any rule logic (ADR-002); output formats (ADR-000 §6.2).

---

## 2. Technology Documentation References

| Library | Context7 ID or official docs | Used for |
|---|---|---|
| `tomllib` | https://docs.python.org/3.12/library/tomllib.html | Reading the TOML configuration |
| `argparse` | https://docs.python.org/3.12/library/argparse.html | `--config`, `--source`, `--data-dir` options |
| `hashlib` | https://docs.python.org/3.12/library/hashlib.html | SHA-256 checksums of inputs |
| DuckDB Python 1.5.6 | https://duckdb.org/docs/stable/clients/python/overview | Connection, query → pandas/Arrow |
| DuckDB Parquet | https://duckdb.org/docs/stable/data/parquet/overview | `read_parquet` with column projection |
| DuckDB CSV | https://duckdb.org/docs/stable/data/csv/overview | `read_csv` with explicit column types |
| DuckDB JSON functions | https://duckdb.org/docs/stable/sql/functions/json | Extracting `maxspeed:*` keys from the `tags` string |
| GeoPandas 1.2.0 I/O | https://geopandas.org/en/stable/docs/user_guide/io.html | `read_file` for the signs GeoJSON, `GeoSeries.from_wkb`, `to_crs` |

No Context7 IDs: Context7 MCP was unavailable (see ADR-000 §2).

---

## 3. Configuration

### 3.1 File format and location

A single TOML file. Its path is taken from, in order: `--config PATH`, environment variable `MAPQA_CONFIG`, otherwise no file (built-in defaults only). A path given explicitly that does not exist is a configuration error (exit 2). Relative paths inside the file are resolved against the file's folder.

`app/mapqa.example.toml` is committed and documents every key with a comment. A user copies it to `app/mapqa.toml` (git-ignored) to customise.

### 3.2 Keys

| Key | Type | Default | Validation | Notes |
|---|---|---|---|---|
| `source.kind` | string | `files` | one of `files`, `databricks`, `public_api` | Selection precedence below |
| `source.files.data_dir` | path | `course-materials/exercise-data/map-qa` relative to the repository root (the folder containing `app/`) | must exist for `files` | |
| `source.databricks.host` | string | none | not validated in Stage 1 | Stage 2; secret-free |
| `source.databricks.warehouse_id` | string | none | not validated in Stage 1 | Stage 2 |
| `source.databricks.schema` | string | `workspace.map_qa` | not validated in Stage 1 | Stage 2 |
| `area.bbox` | array of 4 floats `[min_lon, min_lat, max_lon, max_lat]` | `[19.80, 49.95, 20.15, 50.15]` | min < max, within −180..180 / −90..90 | Signs outside the bbox are skipped with reason `outside_area`; roads are kept whole (PRD §8) |
| `checks.sign_road_distance_m` | float | `20.0` | 1.0 ≤ value ≤ 100.0 | Shown in `summary.md` and `run_summary.json` |
| `checks.main_road_classes` | array of strings | `["motorway","motorway_link","trunk","trunk_link","primary","primary_link","secondary","secondary_link"]` | non-empty, values are OSM `highway` values | Check 1 scope; `tertiary` can be added later (PRD open question 5) |
| `checks.association_excluded_classes` | array of strings | `["service"]` | OSM `highway` values | Roads ignored when associating signs |
| `output.dir` | path | `reports/map-qa/stage1` relative to the repository root | parent folder must be writable | Must not be inside `data_dir` (checked; exit 2) |

Unknown keys are a configuration error (exit 2) naming the key, so typos do not silently fall back to defaults.

**Secrets are never configuration keys.** If the TOML contains any key named `token`, `password`, `secret` or ending in `_token`, the run exits 2 with "secrets must be set in the environment, not in the config file"; the value is not echoed.

### 3.3 Precedence

For every setting: command-line option → environment variable (only `MAPQA_DATA_SOURCE` for `source.kind`) → configuration file → built-in default. The resolved configuration (without secrets) is written to `run_summary.json` under `config`, including which layer supplied `source.kind` (`cli`, `env`, `file`, `default`).

---

## 4. Source Adapters

### 4.1 Interface

`SourceAdapter` (in `sources/base.py`) exposes:

| Operation | Input | Output | Errors |
|---|---|---|---|
| `kind` | — | `files` / `databricks` / `public_api` | — |
| `location()` | — | human-readable location (folder path, schema, endpoints) | — |
| `check()` | — | list of `InputStatus` (name, location, exists, readable, missing columns) | `SourceUnavailable` |
| `checksums()` | — | mapping input name → SHA-256 hex (files only; other adapters return an empty mapping) | `InputError` |
| `load()` | — | `InputBundle` | `InputError`, `SourceUnavailable` |

`adapter_for(config)` returns a `FilesSource` for `files` and raises `SourceNotAvailable("<kind>: not available yet in this version (Stage 2)")` for `databricks` and `public_api`. The CLI maps `SourceNotAvailable` and `SourceUnavailable` to exit 4 and `InputError` to exit 3. There is no fallback to another adapter (AC-05d).

### 4.2 InputBundle

| Field | Content | CRS |
|---|---|---|
| `roads` | GeoDataFrame, schema `OsmRoad` (ADR-000 §5), sorted by `osm_id` | EPSG:2180 |
| `signs` | GeoDataFrame, schema `TrafficSign`, sorted by `sign_id` | EPSG:2180 |
| `photos` | DataFrame of `photos.csv`, sorted by `photo_id` | — (lon/lat kept as columns) |
| `photo_links` | DataFrame of `photo_road_links.csv`, sorted by `photo_id`, `osm_way_id` | — |
| `overture` | DataFrame with `id`, `class` only, sorted by `id` | — |
| `row_counts` | rows read per dataset before any filtering | — |
| `snapshot` | from `sources.json`: OSM `extract_date`, Overture `release`, `created_at`, bbox | — |
| `read_at` | UTC timestamp when reading started (ISO 8601) | — |

This bundle is the Stage 2 parity boundary: a Databricks adapter must return the same column names, dtypes and sort orders.

### 4.3 `files` adapter — required inputs

| Name | File (in `data_dir`) | Required columns / properties | Read with |
|---|---|---|---|
| `osm_roads` | `osm_roads.parquet` | `osm_id`, `osm_version`, `osm_timestamp`, `highway`, `maxspeed`, `oneway`, `bridge`, `tunnel`, `layer`, `name`, `tags`, `geometry` | DuckDB |
| `overture_segments` | `overture_segments.parquet` | `id`, `class`, `geometry` | DuckDB |
| `traffic_signs` | `traffic_signs.geojson` | properties `id`, `object_value`, `first_seen_at`, `last_seen_at`, `image_ids`, `source_url`; Point geometry | GeoPandas |
| `photos` | `photos.csv` | `photo_id`, `longitude`, `latitude`, `heading`, `captured_at`, `author`, `license`, `source` | DuckDB |
| `photo_road_links` | `photo_road_links.csv` | `photo_id`, `osm_way_id`, `distance_m`, `candidate_flags` | DuckDB |
| `snapshot` | `sources.json` | `osm.extract_date`, `overture.release`, `created_at`, `bbox_wgs84` | `json` |

All six are required. Any missing file, unreadable file or missing column → `InputError` naming the file and, for columns, the missing column names (AC-02).

### 4.4 Reading rules

- **OSM tags:** DuckDB extracts the string values of `maxspeed:forward`, `maxspeed:backward`, `maxspeed:conditional`, `maxspeed:variable`, `source:maxspeed` and `maxspeed:type` from the JSON `tags` column into the columns `maxspeed_forward`, `maxspeed_backward`, `maxspeed_conditional`, `maxspeed_variable`, `source_maxspeed`, `maxspeed_type`. The original `tags` string is passed through unchanged. Absent keys become null, never an empty string.
- **Geometry (verified 2026-11-12):** DuckDB 1.5.6 returns the GeoParquet `geometry` column as a native `GEOMETRY` type carrying its CRS (`GEOMETRY('EPSG:4326')` for OSM, `GEOMETRY('OGC:CRS84')` for Overture), **even without the `spatial` extension loaded**; it is not a raw WKB blob. The adapter therefore loads `spatial` and selects `ST_AsWKB(geometry)` explicitly, then decodes the bytes with Shapely and assigns the CRS itself. The `spatial` extension is installed once per user profile (needs internet the first time; already present on the training VM after the profile run); `mapqa check-source` reports a missing extension as a source error (exit 4) with the install hint. All geometries in the snapshot are valid, non-empty LineStrings (OSM, Overture) and Points (signs).
- **CRS:** OSM's GeoParquet metadata states EPSG:4326. Overture's metadata has **no `crs` entry**, which per the GeoParquet specification means OGC:CRS84; the adapter assigns OGC:CRS84 explicitly and does not rely on library defaults. Both use longitude/latitude axis order; transformations use `always_xy` semantics.
- **CSV typing:** columns are read with explicit types; `heading` and `distance_m` as nullable floats, IDs as strings except `osm_way_id` (BIGINT). An empty `heading` stays null (data README: `0.0` is north, not missing; profile: 15 null, 23 zero).
- **Timestamps:** `osm_timestamp` is a string such as `2026-02-21 10:25:53+00:00` and is parsed to a UTC timestamp; `first_seen_at`/`last_seen_at` of signs are parsed to UTC timestamps (needed by `stale_sign`, ADR-002 §6.2). A value that cannot be parsed is an input error naming the file and column.
- **Reprojection:** roads and signs to EPSG:2180 immediately after decoding. Photos are not reprojected (their links are precomputed).
- **Area filter:** signs whose point lies outside `area.bbox` (inclusive edges) are dropped and counted as skipped `outside_area`. No other dataset is filtered by area.
- **Read-only:** files are opened read-only; DuckDB uses an in-memory database (no file created next to inputs); the adapter never writes inside `data_dir`.

### 4.5 Verified Databricks facts (for Stage 2, not implemented in Stage 1)

Verified on 2026-11-12 with one read-only `SELECT count(*)` per table through the SQL Statement Execution API (`query_databricks.py`):

- Environment variables used by the course tooling: `DATABRICKS_HOST`, `DATABRICKS_TOKEN`, `DATABRICKS_SQL_WAREHOUSE_ID` (warehouse of the exercise: `97c455ee6fb1fcb9`, from `DATABRICKS.md`). Stage 2 reads host and warehouse from the TOML (`source.databricks.*`) with these variables as fallback; the token only from the environment.
- Row counts of `workspace.map_qa.*` equal the local files for all five tables (profile §2).
- With `format=JSON_ARRAY` every value arrives as a JSON string (e.g. `"99768"` for a `BIGINT`); the adapter must cast according to the returned `schema` before building the `InputBundle`, otherwise parity (AC-05b) fails on dtypes.
- Geometry columns are `BINARY` WKB (per `DATABRICKS.md`), unlike the native `GEOMETRY` from local GeoParquet; both adapters converge on Shapely geometries decoded from WKB, which is the parity point.
- The first statement may wake a stopped warehouse (`PENDING`/`RUNNING`); the adapter polls the same `statement_id` and never re-submits, and `FAILED`/`CANCELED` is a source error, never an empty result (AC-05d).
- The transfer method for 99,768 rows with geometry (inline chunks vs `EXTERNAL_LINKS`/Arrow) is decided when Stage 2 starts.

### 4.6 Checksums (AC-01)

`checksums()` computes SHA-256 over the raw bytes of the six inputs, streaming in 1 MiB chunks. The pipeline calls it before `load()` and again after writing outputs. A mismatch is a bug: the run exits 1 with "input changed during run: <name>" and outputs stay in the temporary folder (not promoted).

---

## 5. `check-source` command

Runs `adapter_for(config)` and `check()` only. Prints one line per input: name, location, `ok` or the failure reason. Exit 0 when all inputs pass, 3 when any input fails, 4 when the source is not available. Prints no data values and no credentials. For `databricks` / `public_api` in Stage 1 it prints the "not available yet" message and exits 4 without reading any environment variable.

---

## 6. Technical Decisions

### TOML configuration with strict keys
**Status:** Accepted
**Date:** 2026-11-12
**Context:** Users and agents point the tool at a source without changing code (`mvp-scope.md` §3); silent fallbacks on typos would violate "no silent fallback".
**Decision:** TOML read with `tomllib`; unknown keys and secret-looking keys are errors.
**Rejected alternatives:**
- YAML: extra dependency (PyYAML) and implicit typing pitfalls (`no` → false).
- JSON: no comments, harder for humans to maintain.
**Consequences:**
- (+) No dependency; typos fail loudly.
- (-) Adding a key always needs a code change in the validator.
**Review trigger:** Configuration needs includes/profiles per environment.

### Six required inputs, including `sources.json` and Overture
**Status:** Accepted
**Date:** 2026-11-12
**Context:** AC-27 needs snapshot identification; AC-04/AC-05f need row counts for every dataset; Stage 2 parity covers all five tables.
**Decision:** All six files are required even though Stage 1 checks use only four.
**Rejected alternatives:**
- Optional Overture/photos: a run on an incomplete snapshot would look complete.
**Consequences:**
- (+) One rule: incomplete snapshot → exit 3.
- (-) A user cannot run Check 1 alone on a partial folder.
**Review trigger:** A use case for partial snapshots appears.

### Stage 2/3 sources rejected explicitly, not hidden
**Status:** Accepted
**Date:** 2026-11-12
**Context:** `mvp-scope.md` §2 item 2: other values are recognised and rejected with a clear message.
**Decision:** `databricks` and `public_api` are valid configuration values that produce exit 4 and "not available yet".
**Rejected alternatives:**
- Treat them as invalid values (exit 2): would mislead agents into thinking the option does not exist.
**Consequences:**
- (+) `--help` already documents the final contract.
- (-) Users may try them; the message must be explicit.
**Review trigger:** Stage 2 starts.

---

## 7. Diagrams

### Component diagram

```mermaid
classDiagram
    class SourceAdapter {
        <<interface>>
        kind
        location()
        check() InputStatus[]
        checksums() map
        load() InputBundle
    }
    class FilesSource {
        data_dir
        bbox
    }
    class DatabricksSource {
        <<stage2>>
    }
    class PublicApiSource {
        <<deferred>>
    }
    class InputBundle {
        roads
        signs
        photos
        photo_links
        overture
        row_counts
        snapshot
        read_at
    }
    SourceAdapter <|.. FilesSource
    SourceAdapter <|.. DatabricksSource
    SourceAdapter <|.. PublicApiSource
    FilesSource --> InputBundle : load()
```

### Configuration resolution

```mermaid
sequenceDiagram
    participant CLI as cli
    participant CFG as config
    participant ENV as environment
    participant FS as TOML file
    CLI->>CFG: resolve(args)
    CFG->>ENV: MAPQA_CONFIG?
    alt --config given
        CFG->>FS: read --config path
    else MAPQA_CONFIG set
        CFG->>FS: read MAPQA_CONFIG path
    else none
        CFG->>CFG: defaults only
    end
    CFG->>CFG: reject unknown / secret-looking keys (exit 2)
    CFG->>ENV: MAPQA_DATA_SOURCE?
    CFG->>CFG: source.kind = cli ?? env ?? file ?? "files"
    CFG-->>CLI: RunConfig + provenance of source.kind
```

### Loading inputs

```mermaid
sequenceDiagram
    participant P as pipeline
    participant F as FilesSource
    participant D as DuckDB (in-memory)
    participant G as GeoPandas
    P->>F: check()
    F-->>P: all six inputs ok
    P->>F: checksums() (before)
    P->>F: load()
    F->>D: LOAD spatial
    F->>D: read_parquet(osm_roads), ST_AsWKB(geometry), extract maxspeed* from tags
    D-->>F: frame with WKB bytes
    F->>G: decode WKB, assign EPSG:4326 explicitly, to_crs(2180)
    F->>D: read_parquet(overture) id, class
    F->>G: read_file(traffic_signs.geojson), to_crs(2180), bbox filter
    F->>D: read_csv(photos), read_csv(photo_road_links) with explicit types
    F-->>P: InputBundle (sorted, row_counts, snapshot, read_at)
```

---

## 8. Testing Strategy

### Test scenarios for this area

| Scenario | Type | Input | Expected output | Edge cases |
|---|---|---|---|---|
| Source precedence | Integration (CLI) | `--source files` + `MAPQA_DATA_SOURCE=databricks` | exit 0, `run_summary.json` `config.source_kind_from = cli` | env only → `env`; file only → `file`; nothing → `default` |
| Unknown config key | Integration (CLI) | TOML with `checks.sign_road_distanse_m` | exit 2, stderr names the key | — |
| Secret in config | Integration (CLI) | TOML with `source.databricks.token = "x"` | exit 2, stderr does not contain `x` | — |
| Stage 2 source | Integration (CLI) | `--source databricks` for `run` and `check-source` | exit 4, "not available yet" | `public_api` same |
| Missing input | Integration (CLI) | copy of data dir without `photos.csv` | exit 3, stderr names `photos.csv`, no queue | missing column in a CSV → names the column |
| Tag extraction | Unit (part of ADR-002 case 1 fixtures) | real rows with `maxspeed:forward` in `tags` | `maxspeed_forward` populated, `tags` unchanged | key absent → null, not "" |
| CRS | Unit | fixture road | frame CRS is EPSG:2180 | — |
| Geometry type from DuckDB | Integration | real `osm_roads.parquet`, `overture_segments.parquet` | adapter yields Shapely LineStrings; Overture CRS assigned as OGC:CRS84 although metadata has none | `spatial` extension missing → exit 4 with install hint |
| Timestamps | Unit | fixture road and sign | `osm_timestamp`, `last_seen_at` parsed to UTC | unparseable timestamp → input error naming the column |
| Checksums | Integration | real snapshot | before == after, recorded per input | — |

### Technical acceptance criteria

- **TAC-001-01** `mapqa check-source` on the pinned snapshot prints six `ok` lines and exits 0.
- **TAC-001-02** Row counts in `run_summary.json` equal `sources.json`: OSM 99,768; Overture 85,405; photos 561; and 1,730 traffic signs.
- **TAC-001-03** No file in `data_dir` has a modification time or checksum change after a run.
- **TAC-001-04** No configuration, log, stdout or output file contains the value of any environment variable whose name ends in `_TOKEN` or `_KEY` (asserted by setting a sentinel value in the integration test).
