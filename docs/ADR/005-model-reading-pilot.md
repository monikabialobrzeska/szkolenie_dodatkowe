# ADR-005: Model Reading Pilot (Stage 3)

**Date:** 2026-11-13
**Status:** Accepted 2026-11-13 (trade-offs in §6 confirmed with the user); implemented `b3574c3`, `048baae`
**Relates to:** PRD AC-29–AC-40, §11, §12 "Models" / "Pilot design", open questions 6 and 9; `mvp-scope.md` §2 items 11–13; [`000-main-architecture.md`](000-main-architecture.md) (CLI, exit codes)

---

## 1. Scope

A separate `mapqa pilot` command group that reads sign crops from `course-materials/exercise-data/map-qa/signs/crops/` with three OpenRouter models, composes the escalation chain, and writes an evaluation report with explicit denominators. Offline evaluation only: nothing is written to the review queue (AC-40 deferred).

Not covered: queue integration, verdicts, other sign types, prompt tuning, any model other than the three below.

## 2. Evidence (captured 2026-11-13, `app/tests/fixtures/openrouter/`)

| Capture | Result |
|---|---|
| `jev_decision.json` — `typesafe/jev-1.14`, `POST /api/alpha/decisions` | HTTP 200, 0.62 s; `answers.speed_limit = {type: choice, choice: "20", probabilities: {<19 options>}, confidence: 1}`; `usage.cost_details.upstream_inference_cost` 4.3e-5 USD |
| `luna_decisions_decision.json` — `openai/gpt-6-luna-decisions`, same endpoint | HTTP 200, 1.38 s; same answer shape; resolved model `openai/gpt-6-luna-decisions-20261006` |
| `luna_chat.json` — `openai/gpt-6-luna`, `POST /api/v1/chat/completions`, `reasoning.effort = medium`, strict JSON schema | HTTP 200, 3.16 s; `choices[0].message.content = "{\"answer\":\"20\",\"uncertain\":false}"`, `finish_reason = stop`, `reasoning_tokens` 0 |
| `error_unknown_model.json` — unknown model id (free) | HTTP 400 `application/json`, body `{"error": {"message": "...", "code": 400}}`, 0.38 s |

Observations that shape the decisions below:
- `state` as a list of content parts was accepted on the first attempt by both decision models; the object form was never needed.
- Both decision models returned a degenerate distribution (1 for the chosen option, 0 elsewhere) on a clear crop. The threshold may therefore rarely trigger; the report must show the probability histogram so this is visible, not hidden.
- The decisions endpoint is `alpha`; its shape may change. The parser validates every field and treats anything unexpected as `invalid_response` (an error, never an answer).

Data facts (`signs/labels.csv`, `signs/signs.csv`, 119 crops): 115 `certain`, 4 `uncertain`; 103 sequences; numeric values 20–120 (6–9 certain crops each); only **1** certain `zone` crop. The Mapillary detector agrees with the label on 89/89 numeric crops; it is wrong only on `cancelled_sign` (crossed-out 40 and 80) and on one `false_positive_object` (a logo detected as 100).

## 3. Component Design

New package `src/mapqa/pilot/`, no new dependency (`urllib`, like `databricks_client.py`).

| Module | Responsibility | Reads labels? |
|---|---|---|
| `sample.py` | Builds the stratified manifest (§4.1) from `signs.csv` + `labels.csv`. Writes `manifest.json` (no label data) and `strata.json` (evaluator only). | yes (evaluation side) |
| `openrouter.py` | `post(path, body) -> Exchange`: one HTTP call, 40 s timeout, no retries; `Exchange(http_status, headers, body, elapsed_s, error)`; key only from `OPENROUTER_API_KEY`, never stored. | no |
| `reader.py` | Request builders (§4.2), response parsers (§4.3), cache (§4.5), per-model budget and stop-on-error (§4.6), call log. Input: `manifest.json` + crop bytes. | **no** (AC-37) |
| `chain.py` | Pure function: tier-1 reading + tier-2 reading → final result and tier (§4.4). | no |
| `evaluate.py` | Joins readings with `labels.csv` and the detector value; metrics with denominators; tier-1 model selection (§5). | yes |
| `report.py` | `report.md` + `report.json`. | — |

## 4. Data and Interface Contracts

### 4.1 Sample manifest (AC-33, AC-39; `mvp-scope.md` §2 item 12, composition revised 2026-11-13)

20 crops, all with `status = certain`, **at most one crop per Mapillary `sequence`** (no neighbouring frames). Deterministic order: `rank = sha256("mapqa-pilot-v1:" + photo_id)`, ascending. Strata are filled in this order (scarcest first), each taking the lowest-rank crop whose sequence is still unused:

| Stratum | Pool (`negative_type` / `sign_value`) | n |
|---|---|---|
| zone | `zone` (only one certain crop exists) | 1 |
| zone end / limit end | `zone_end`, `limit_end` | 2 |
| other prohibitory | 1 from `no_entry`, `weight`, `no_overtaking`, `no_stopping`; 1 `cancelled_sign` | 2 |
| no speed sign | 2 `no_speed_sign`; 1 `false_positive_object` whose detector class is a maximum-speed-limit sign | 3 |
| numeric | one per value 20, 30, …, 120 (11), then the lowest-rank remaining numeric crop | 12 |

Two negatives (cancelled sign, logo) are cases where the detector is wrong, so the "better than the detector on negatives" comparison is not vacuous. If a stratum cannot be filled, `pilot plan` fails with exit 3 and names the stratum; it never substitutes silently.

`manifest.json`: `{version, models, budget_per_model, calls_planned, threshold, items: [{item_id, crop, crop_sha256, sequence}]}` — deterministic (no timestamp, crop paths relative to the data dir), so re-running `pilot plan` yields the same confirmation code. `item_id` is the photo id; it is used for joining and file lookup only and is never sent to a model (AC-29). The **confirmation code** is the first 12 hex digits of the SHA-256 of `manifest.json`; `pilot run` refuses to start unless `--confirm` matches it (AC-33).

### 4.2 Requests (AC-29, AC-30)

Exactly the request bodies captured in S3-1 (`capture.py`): image as a base64 `data:image/jpeg` URL from the crop bytes, the neutral question, answer options = `5,10,15,20,25,30,40,50,60,70,80,90,100,110,120,130,140`, `none`, `unreadable`.

| Model | Endpoint | Body |
|---|---|---|
| `typesafe/jev-1.14`, `openai/gpt-6-luna-decisions` | `POST /api/alpha/decisions` | `state` = list with one `image_url` part; one `choice` question `speed_limit` with criteria per option |
| `openai/gpt-6-luna` | `POST /api/v1/chat/completions` | text + image; `reasoning.effort = medium`; strict `json_schema` `{answer: enum, uncertain: boolean}` |

### 4.2a Image preparation (added 2026-11-13 after the first pilot call, user decision)

The first pilot call (Jev, crop 155×192 px) returned HTTP 400 `{"detail": {"error_type": "api_usage_error", "message": "Invalid image dimensions."}}` (no cost); the S3-1 crop of 311×308 px was accepted. The minimum is not documented. Every crop is therefore scaled before sending, **identically for all three models** (`pilot/image.py`): shorter side ≥ 320 px, longer side ≤ 1600 px (the cap wins), aspect ratio kept, Pillow 12.0.0 `LANCZOS`, JPEG quality 92; a crop already in range is sent byte-identical. Only the size changes. The settings are part of `manifest.json` (so of the confirmation code); the cache key uses the hash of the bytes actually sent; `calls.jsonl` records `sent_px` and, for errors, the API message (AC-36). New dependency: `pillow==12.0.0` (the version the crops were built with).

Nothing else is sent: no file name, photo id, sequence, label, detector value or map attribute. A unit test asserts that the serialized body contains none of the manifest's identifiers.

### 4.3 Reading record

`Reading{model, item_id, status, answer, probability, uncertain, raw_choice, http_status, elapsed_s, generation_id, cost_usd, cached}` with `status` ∈:

| Status | Meaning |
|---|---|
| `answered` | valid model output, accepted at this tier |
| `abstained` | decision model, valid output, `max(probabilities) < 0.7` (system decision, AC-35) |
| `unreadable` | model answered `unreadable` (model output, distinct from `abstained`) |
| `uncertain` | chat model answered with `uncertain = true` |
| `invalid_response` | HTTP 200 but the body breaks the contract (missing field, choice not in options, `choice ≠ argmax`, probabilities not summing to 1 ± 0.01, non-JSON content, `finish_reason ≠ stop`) |
| `model_unavailable` | HTTP ≠ 200, timeout or network error (AC-32) |
| `not_attempted` | the paid loop stopped after an error (§4.6) |
| `budget_exhausted` | the model's 25 calls were used (AC-31) |

Only `answered` carries an `answer` for metrics. Errors are never answers.

### 4.4 Chain (AC-35a–AC-35d)

- Tier 1 (selected decision model, §5): `answered` with a value or `none` → final, tier `decision_model`. `abstained` → tier 2. Everything else (incl. tier-1 `unreadable`) → human, reason `model_unresolved` (or `budget_exhausted`).
- Tier 2 (`openai/gpt-6-luna`), **rule for PRD open question 9**: accepted only if HTTP 200, `finish_reason = stop`, content parses to exactly `{answer, uncertain}`, `answer` ∈ values ∪ {`none`} and `uncertain = false`. `unreadable`, `uncertain = true`, invalid output, timeout or error → human, reason `model_unresolved`. Tier 2 never receives tier-1 output (the request is identical to §4.2).
- Tier 3 `human`: the item is listed in the report's "manual queue" table with both tiers' outputs; no value is filled in.

**Pilot reuse:** in Part A Luna reads all 20 crops with the identical tier-2 request, so Part B takes tier-2 readings from the cache: Part B makes **no new calls**. The chain only reads Luna's result for tier-1 abstentions; the report states this.

### 4.5 Cache (AC-34)

Key = SHA-256 of canonical JSON (`sort_keys`, no spaces) of `{endpoint, body}`, with the image data URL replaced by `sha256:<crop sha256>`. The key covers model, question, options and reasoning level. Every HTTP 200 exchange (including `invalid_response`) is stored in `reports/map-qa/pilot/cache/<key>.json` as `{http_status, headers (content-type, x-generation-id), elapsed_s, body}`. Errors are not cached. A cache hit makes no call and is not counted.

### 4.6 Budget, timeout, errors (AC-31, AC-32, AC-36)

- Counter per model per run; every HTTP attempt counts (success, error, timeout). At 25 the model's remaining items get `budget_exhausted`. The planned Part A spend is 20 per model.
- 40 s timeout, no retries.
- **After the first `model_unavailable` (any model), no further paid calls are made in this run** (user decision 2026-11-13). Remaining uncached items get `not_attempted`. A rerun resumes from the cache at no extra cost.
- `calls.jsonl` (append): `{run_id, model, item_id, cache_key, cached, status, http_status, elapsed_s, generation_id, cost_usd}`. No image, no key. Before any write, the record is checked for the key string (the sentinel check from `capture.py`).

### 4.7 CLI

```
mapqa pilot plan     [--data-dir D] [--output-dir O]     # free; writes and prints the manifest + confirmation code
mapqa pilot run      --confirm CODE [--data-dir D] [--output-dir O]   # paid; Part A readings only
mapqa pilot evaluate [--data-dir D] [--output-dir O]     # free; reads labels, selects tier 1, composes the chain, writes the report
```

Default output: `reports/map-qa/pilot/`. Exit codes follow ADR-000: 2 = wrong or missing `--confirm`; 3 = missing crop, hash mismatch, manifest or readings missing, unfillable stratum; 4 = key missing or malformed, or a `model_unavailable` occurred (records are still written). `pilot run` checks `crop_sha256` before sending.

## 5. Metrics and Tier-1 Selection (AC-35e, AC-38, `mvp-scope.md` §2 item 13)

All metrics only over items with `status = certain` (20/20 by construction), each printed with its denominator.

Per model (Part A side by side) and for the detector baseline:
- **Accuracy before abstention**: `raw_choice == label` over items with valid output (decision models; Luna: its answer).
- **Accuracy after abstention**: correct over `answered`.
- **Coverage**: `answered` / items. **Abstentions**, `unreadable`, `uncertain`, errors: counts.
- **Negatives** (8): wrong numeric value on a `none` label — the key comparison against the detector. **Positives** (12): accuracy.
- Probability histogram per decision model.
- Detector baseline: `regulatory--maximum-speed-limit-<N>--g*` → `N`; any other class or missing → `none`; always "answers".

**Tier-1 selection (fixed before the run, user decision 2026-11-13):** the decision model with more correct `answered` items; tie → fewer wrong values on negatives; tie → `typesafe/jev-1.14`. Because selection and chain evaluation use the same 20 items, the report flags the chain result as optimistic.

Chain (Part B): per tier, items received / resolved / passed on, accuracy over resolved; end-to-end accuracy and the share that reached the human queue.

The threshold **0.7 is fixed here and not tuned** (PRD open question 6): a validation split is not affordable within 25 calls per model. The report states this.

## 6. Technical Decisions

| Decision | Choice | Rejected | Why |
|---|---|---|---|
| Negative composition | all certain; 1 zone, 2 end, 2 prohibitory (incl. cancelled), 3 no sign (incl. logo) | 2 zones incl. 1 uncertain (literal `mvp-scope`) | full 20/20 denominator; includes the cases where the detector is wrong (user, 2026-11-13) |
| Scope of stop-on-error | stop all models | stop only the failing model | literal AC-32; cache makes resuming free (user, 2026-11-13) |
| Tier-1 choice | more correct, then negatives, then Jev | always Jev | fair comparison, fixed in advance (user, 2026-11-13) |
| Tier-2 uncertainty | explicit `uncertain` boolean in a strict schema | self-reported confidence number; only `unreadable` | matches the S3-1 capture; a boolean is unambiguous, LLM confidence numbers are uncalibrated |
| Part B tier-2 calls | reuse Part A Luna readings (identical request) | separate calls | AC-34; saves up to 20 calls; the result is identical by construction |
| Label isolation | the reader gets `manifest.json` only; test runs `pilot run` on a data dir without `labels.csv` | code review only | AC-37 checkable |
| Small crops | scale all crops to 320–1600 px for every model | only crops ≥ 300 px; probe the minimum first | keeps the agreed sample; same input for all models (user, 2026-11-13) |
| Confirmation | code = hash of the manifest | `y/N` prompt | binds consent to exactly this manifest; works for agents without a TTY |

## 7. Testing Strategy

Unit (no network; fake transport replays the four captures): request builders match the captured `request` (except the image placeholder); parsers on each capture; synthetic variants derived from captures (`# synthetic`: probability 0.6 → `abstained`, choice ≠ argmax → `invalid_response`, `uncertain: true`, non-JSON content); HTTP 400 and timeout → `model_unavailable` and the loop stops for all models; budget 25 (26th call → `budget_exhausted`); cache hit makes no call and is not counted; no identifier in any request body; key sentinel; sampling (strata sizes, one crop per sequence, determinism, unfillable stratum → exit 3); chain table; metrics with denominators on a hand-built readings table; tier-1 selection tie-breaks.

Integration: `pilot plan` on the real data; `pilot run` with `--confirm` mismatch → exit 2 and no call; `pilot run` with a fake transport on a data dir without `labels.csv`. **The paid run is manual QA (S3-5)**, not part of `pytest`.

### Technical acceptance criteria
- `manifest.json` has 20 certain items, 20 distinct sequences, the strata in §4.1.
- `pilot run` makes ≤ 25 calls per model, records every call, and stores no secret.
- `report.md` shows every metric with its denominator, the 0.7 threshold, the probability histogram, the detector baseline and the "optimistic chain" note.
