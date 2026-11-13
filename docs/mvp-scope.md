# MVP Scope Decision — Prototype A (Speed-Limit Conflict Finder)

Status: accepted (revised 2026-11-12) · Date: 2026-11-03 · Companion to `docs/PRD.md`

This document records which parts of the PRD are built first, which are deferred, and why — based on a profile of the data actually available in `course-materials/exercise-data/map-qa/`. Every number below can be reproduced with the queries in the appendix. The ADR decides *how* to build it; this document decides *what* is built first.

---

## 1. What the data shows

### 1.1 Speed limits in OSM (99,768 ways)

| Finding | Number | Consequence |
|---|---|---|
| Ways with `maxspeed` | 16,290 | Enough to test the pipeline on real data. |
| `maxspeed` values that are not a plain integer (km/h) | **0** | No `mph`, `PL:urban` or free text in the `maxspeed` value itself in this snapshot. The "unparseable value" check will find nothing here. |
| Implicit source in `source:maxspeed` / `maxspeed:type` (`PL:urban`, `PL:zone30`, `PL:rural`…) | ~12,000 tags | Keep as evidence (shows *why* the value is what it is); not needed to interpret the number. |
| Direction-specific (`maxspeed:forward` / `:backward`) | 261 / 247 ways | Must be handled: comparing a sign to the plain `maxspeed` is wrong on these ways. |
| Conditional (`maxspeed:conditional`, e.g. `110 @ (Mo-Fr 06:00-18:00)`) | 92 ways (all motorway) | → `needs_review`, never `conflict`. |
| Variable (`maxspeed:variable=yes`) | 82 ways | → `needs_review`. |
| Main roads (motorway/trunk/primary/secondary + links) with no limit at all, excluding directional tagging | **290** | High-value, fully deterministic check. |

### 1.2 OSM vs Overture

| Finding | Number |
|---|---|
| Overture segments with an OSM way reference in `sources` | 84,614 of 85,405 |
| OSM ways linked to at least one Overture segment | 99,416 of 99,768 |
| Linked pairs where both have an unconditional, whole-segment limit | 17,071 |
| … of which the values differ | **34 (32 ways)** |
| … of those 34, where the OSM way is newer than the version Overture used | **34 of 34** |
| Overture segments with partial (`between`) or conditional (`when`) speed rules | 4,065 |

**Consequence:** Overture's speed limits come from OSM. In this snapshot every disagreement is explained by Overture using an older OSM version. The check measures snapshot lag, not map errors, and Overture is not an independent second opinion. **Deferred.**

### 1.3 Mapillary signs vs OSM roads (1,730 sign objects)

| Finding | Number |
|---|---|
| Sign objects with a parsed limit value | 1,730 (most common: 70, 40, 50, 30 km/h) |
| Distance to nearest non-service road: median / 90th percentile | 5.4 m / 14.7 m |
| Signs with a non-service road within 20 m | 1,625 |
| … with **exactly one** candidate road within 20 m | **421** (the rest are ambiguous) |
| … of those 421, road has a `maxspeed` | 330: **170 equal, 160 differ**; 91 roads without `maxspeed` *(first profile; superseded by §1.5)* |

Cross-check on the curated sign set (`signs/`): for 71 certain numeric labels with a nearest OSM road that has `maxspeed`, only **25 match**.

**Consequence:** "nearest road" is often not the road the sign governs (a sign at the start of the next way, a side road, a slip road, the other carriageway; Mapillary sign objects have no facing direction). About half of the naive comparisons disagree, and most of these are probably not map errors. In the MVP every sign-vs-road difference is `needs_review`. A manually checked sample decides whether some of them can later become `conflict`.

### 1.4 Photos and the decision model

| Dataset | Content |
|---|---|
| `signs/` crops | 119 crops in 103 sequences (max 3 per sequence). Labels: 115 `certain`, 4 `uncertain`. Certain: 89 numeric values (20–120, ~6–9 per value), 25 `none` (zones, zone ends, limit end, no entry, weight, …), 1 `unreadable`. |
| Mapillary detector vs label | Certain numeric: **89/89 match**. Certain `none`: the detector still reports a limit on **5/25**. |
| `reference_labels.csv` (general photos) | `speed_limit_value`: only 14 certain (8 `none`, 5×70, 1×30). Too few for evaluation. |
| `photos.csv` / `photo_road_links.csv` | 561 photos, 557 linked through the OSM reference. Flags: 65 ambiguous candidates, 35 camera against way direction, 31 panoramas, 15 without heading. |

**Consequences:**

- The sign crop set is the only usable evaluation set for the model. The general photos are not.
- The numeric positives were selected from Mapillary detections, so the detector looks perfect on them by construction. The real test is the **negatives**: zone signs, zone ends and limit ends, where the detector errs on 5/25. A model is worth adding only if it does better there without losing the positives.
- With a 25-call budget, the model can be piloted but not calibrated. Results are reported with explicit denominators and no claims of statistical significance.

### 1.5 Findings of the full data profile (2026-11-12)

Details and queries: `reports/map-qa/profile.md`. Local files and the five Databricks tables have identical row counts.

| Finding | Number | Consequence |
|---|---|---|
| Signs with exactly one non-service road within 20 m | 420: 180 equal, 162 differ, 78 road without limit | Supersedes §1.3 (directional tags respected) |
| … of the 240 not equal, sign last seen before the road's last OSM edit | **191** | New reason `stale_sign` |
| `source:maxspeed` / `maxspeed:type` contradicts `maxspeed` (e.g. `PL:urban` + `70`) | **161 ways** (102 main roads) | New check `tag_inconsistency` in Stage 1 |
| `tunnel=building_passage` among `tunnel` tags | 605 of 827 | Not treated as another level |
| Ways with a limit in one direction only, no `maxspeed` | 58 | Other direction = unknown |
| Distinct OSM ways with any linked photo | 230 of 99,768 | Most candidates have no photo |
| Signs last seen before 2025 | 1,200 of 1,730 | Sign age shown as evidence |

---

## 2. Decision: what the MVP contains

The MVP is a **command-line tool with a read-only static viewer** (item 8a, ADR-003; no verdict recording). It produces files that an analyst opens in QGIS or a spreadsheet, and that an agent can read.

### Stage 1 — Workflow on local files (build first)

1. **CLI** with a run command and `--help`. It also writes a machine-readable run summary (JSON), so another agent can call the tool and read the result.
2. **Data-source configuration** (see §3). Only `files` is implemented in Stage 1. Other values are recognised and rejected with a clear "not available yet" message.
3. **Speed-limit interpretation:** numeric `maxspeed`, `maxspeed:forward` / `:backward`, conditional and variable flags. Raw tags are kept. A value that cannot be interpreted is flagged (0 such values expected in this snapshot; covered by test fixtures).
4. **Check 1 — Missing limit on main roads:** state `conflict`. Expected order of magnitude: ~290 ways.
5. **Check 2 — Sign vs road:** a sign with exactly one non-service road within the distance limit and a value different from that road's limit (respecting directional tags) → state `needs_review`, reason `sign_road_mismatch`. More than one candidate road, a conditional or variable limit, a bridge or tunnel, or a sign older than the road's last OSM edit (`stale_sign`) → `needs_review` with the specific reason. An equal value → no candidate.
5a. **Check 4 — `tag_inconsistency`** (numbering as in ADR-002 §6) (added 2026-11-12 after the data profile): `source:maxspeed` / `maxspeed:type` names a Polish implicit category whose allowed values do not include the numeric `maxspeed` (e.g. `PL:urban` with `70`) → state `conflict`. Expected order of magnitude: ~161 ways (102 on main roads).
6. **Evidence:** source IDs, raw and interpreted values, distance, sign dates, and the IDs and count of all photos linked to the road.
7. **Ranking and stable candidate IDs** as in PRD AC-19 and AC-20.
8. **Outputs:** review queue as CSV and GeoJSON (opens in QGIS), summary report in Markdown, and a run summary in JSON.
8a. **Read-only viewer** (added 2026-11-12): `viewer.html` next to the outputs — summary, filterable candidate table, map, candidate detail with evidence. English UI. See ADR-003.

### Stage 2 — Databricks as a second source (built 2026-11-13, ADR-004; parity test green)

9. Implement `databricks` in the same configuration: read-only access to the five `workspace.map_qa` tables.
10. **Parity test:** the same checks on `files` and on `databricks` must give identical row counts and an identical queue (PRD AC-05b). This proves the source layer does not change results.

### Stage 3 — Decision-model pilot (offline evaluation, not wired into the queue)

11. A separate CLI command reads the sign crops and runs the escalation chain (PRD AC-29 to AC-39): decision model, then general LLM with medium reasoning for abstentions only, then manual queue.
12. **Pilot sample:** 20 tier-1 calls plus up to 5 tier-2 calls (25 in total). The sample is stratified and split by sequence: 12 numeric values across the value range and 8 negatives (at least 2 zone, 2 zone end or limit end, 2 other prohibitory signs, 2 without a speed sign). The abstention threshold is fixed before the run (proposed: 0.7) and not tuned on the same sample.
13. **Report:** accuracy and coverage per tier and end to end over certain labels, with denominators. Comparison with the **Mapillary detector baseline** on the same items, especially the negatives. Count of items sent to the manual queue.

### Deferred (next increments, in suggested order)

| Item | Reason for deferring |
|---|---|
| UI with verdict recording (dashboard) | A **read-only static viewer** (map + table + candidate detail) was moved into Stage 1 on 2026-11-12 at the user's request (ADR-003). Recording verdicts in the UI stays deferred. |
| Recording verdicts and carrying them over (AC-23, AC-24) | Needs a UI or an agreed file workflow. Stable IDs are built now so verdicts can be added without breaking anything. |
| Promoting sign-vs-road to `conflict` | Only after a manual sample of ~30 candidates shows acceptable precision. |
| Using model readings in the review queue (AC-40) | Only after the pilot beats the detector baseline on negatives. |
| OSM vs Overture check | All 34 disagreements are Overture lagging behind OSM; not independent evidence. |
| `public_api` source | Needs snapshot building, link computation, URL refresh and tokens. Highest effort, lowest value for testing the workflow. |
| Tertiary roads in "missing limit" | Open question 5; easy to add once the main check is stable. |

---

## 3. Data-source configuration (functional contract)

So that a person or another agent can point the tool at any source without changing code:

- **Selection:** `files`, `databricks` or `public_api`. Precedence: command-line option, then the environment variable `MAPQA_DATA_SOURCE`, then the config file, then the default `files`.
- **Config file:** a single file (path given by `MAPQA_CONFIG` or a command-line option). For `files` it holds the data folder. For `databricks` it holds the host, the SQL warehouse ID and the `catalog.schema` (default `workspace.map_qa`). It also holds the area, the distance limit and the output folder.
- **Secrets only in the environment:** `DATABRICKS_TOKEN` (and later `MAPILLARY_TOKEN`, `OPENROUTER_API_KEY`) are never in the config file, the repository, logs or outputs. The tool checks only whether they are present.
- **Self-describing for agents:** `--help` lists sources and options. A `check-source` command verifies reachability and credentials without running the checks. The run summary JSON states the source, location, read time and row counts. Exit codes distinguish success, input error and source error.
- **No silent fallback:** if the chosen source fails, the run stops (PRD AC-05d).

The ADR decides the file format, library choices, and how large Databricks tables are transferred (99,768 OSM rows with geometry exceed simple inline query results; filtering on the server side or paged/Arrow transfer is a decision for the ADR).

---

## 4. How we test the workflow at MVP stage

| Level | What | Pass condition |
|---|---|---|
| Unit (TDD) | Interpretation of numeric and directional limits and conditional/variable flags; one-candidate vs ambiguous sign association; heading 359/1; bridge or tunnel; stable ID | Red first, then green; at most 6 cases per stage, built from real rows |
| Integration | Full CLI run on local files | Exit 0; outputs written; inputs unchanged (checksums); same output on a second run |
| Reproducibility | Every count in the summary | Re-derived with one SQL query each (appendix); matches the order of magnitude in §1 |
| Source parity (Stage 2) | `files` vs `databricks` | Identical counts and queue |
| Manual precision sample | 30 random `sign_road_mismatch` candidates reviewed by a person against photos and imagery | Share of real map problems recorded; decides promotion to `conflict` |
| Model pilot (Stage 3) | 20 + 5 calls on stratified sign crops | Per-tier and end-to-end accuracy and coverage with denominators; better than the detector on negatives, with no loss on positives, before any queue integration |

---

## Appendix — profile queries (DuckDB, run from `course-materials/exercise-data/map-qa/`)

```sql
-- non-numeric maxspeed values
SELECT count(*) FROM 'osm_roads.parquet' WHERE maxspeed IS NOT NULL AND NOT regexp_full_match(maxspeed, '\d+');

-- main roads without any limit (excluding directional tagging)
SELECT highway, count(*) FROM 'osm_roads.parquet'
WHERE maxspeed IS NULL
  AND highway IN ('motorway','trunk','primary','secondary','motorway_link','trunk_link','primary_link','secondary_link')
  AND tags NOT LIKE '%"maxspeed:forward"%' AND tags NOT LIKE '%"maxspeed:backward"%'
GROUP BY 1;

-- OSM vs Overture disagreements and OSM version lag
WITH l AS (
  SELECT DISTINCT v.id,
         try_cast(regexp_extract(s.record_id, '^w(\d+)', 1) AS BIGINT) AS osm_id,
         try_cast(regexp_extract(s.record_id, '@(\d+)', 1) AS INT)     AS ov_version,
         list_filter(v.speed_limits, lambda x: x."when" IS NULL AND x."between" IS NULL) AS u
  FROM 'overture_segments.parquet' v, unnest(v.sources) t(s)
  WHERE s.dataset = 'OpenStreetMap' AND coalesce(s.property, '') = '' AND s.record_id LIKE 'w%')
SELECT o.osm_version > l.ov_version AS osm_newer, count(*)
FROM l JOIN 'osm_roads.parquet' o USING (osm_id)
WHERE o.maxspeed IS NOT NULL AND len(l.u) = 1 AND CAST(o.maxspeed AS INT) <> l.u[1].max_speed.value
GROUP BY 1;
```

The sign-vs-road figures were computed in EPSG:2180 with GeoPandas: nearest non-service OSM way within 50 m, and count of distinct ways within a 20 m buffer.
