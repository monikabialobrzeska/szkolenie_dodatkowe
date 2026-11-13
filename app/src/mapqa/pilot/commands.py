"""`mapqa pilot plan | run | evaluate` (ADR-005 §4.7). Exit codes follow ADR-000."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from mapqa.config import DEFAULT_DATA_DIR, REPO_ROOT
from mapqa.pilot import image
from mapqa.pilot.evaluate import evaluate
from mapqa.pilot.openrouter import ApiKeyError, HttpTransport, api_key_from_env
from mapqa.pilot.reader import BUDGET_PER_MODEL, MODELS, THRESHOLD, Item, Reader
from mapqa.pilot.report import SHORT, render
from mapqa.pilot.sample import SampleError, build_sample

DEFAULT_PILOT_DIR = REPO_ROOT / "reports" / "map-qa" / "pilot"
# upstream cost per call seen in the S3-1 captures (USD), for the manifest's estimate only
CAPTURED_COST = {"typesafe/jev-1.14": 4.326e-05, "openai/gpt-6-luna-decisions": 4.89e-05, "openai/gpt-6-luna": 4.29e-05}


def make_transport(api_key: str):
    return HttpTransport(api_key)


def confirm_code(manifest_path: Path) -> str:
    return hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()[:12]


def _fail(step: str, message: str, code: int) -> int:
    print(f"error: pilot {step}: {message}", file=sys.stderr)
    return code


def _dirs(args) -> tuple[Path, Path]:
    data = Path(args.data_dir).resolve() if getattr(args, "data_dir", None) else DEFAULT_DATA_DIR
    out = Path(args.output_dir).resolve() if getattr(args, "output_dir", None) else DEFAULT_PILOT_DIR
    return data, out


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def cmd_plan(args) -> int:
    data, out = _dirs(args)
    try:
        signs = pd.read_csv(data / "signs" / "signs.csv", dtype=str)
        labels = pd.read_csv(data / "signs" / "labels.csv", dtype=str)
        items, strata = build_sample(signs, labels)
        Reader.load_crops([Item(**i) for i in items], data)  # every crop present and unchanged
    except (OSError, KeyError) as exc:
        return _fail("plan", f"sign inputs unreadable in {data / 'signs'} ({exc})", 3)
    except (SampleError, ValueError) as exc:
        return _fail("plan", str(exc), 3)
    manifest = {"version": 1, "models": list(MODELS), "budget_per_model": BUDGET_PER_MODEL,
                "calls_planned": len(items) * len(MODELS), "threshold": THRESHOLD, "image": image.SETTINGS,
                "items": items}
    _write_json(out / "manifest.json", manifest)
    _write_json(out / "strata.json", strata)
    code = confirm_code(out / "manifest.json")
    estimate = sum(CAPTURED_COST.values()) * len(items)
    print(f"pilot manifest: {out / 'manifest.json'}")
    print(f"{'#':>2}  {'item':<28} {'sequence':<24} stratum")
    for n, item in enumerate(items, 1):
        print(f"{n:>2}  {item['item_id']:<28} {item['sequence']:<24} {strata[item['item_id']]}")
    print(f"strata: " + ", ".join(f"{k} {v}" for k, v in Counter(strata.values()).items()))
    print(f"models: " + ", ".join(f"{SHORT[m]} ({m})" for m in MODELS))
    print(f"paid calls planned: {manifest['calls_planned']} ({len(items)} per model; budget {BUDGET_PER_MODEL} per model; "
          f"cache hits are free)")
    print(f"estimated upstream cost: ~${estimate:.4f} (S3-1 capture prices)")
    print(f"sent to the models: crop scaled to {image.MIN_SIDE}-{image.MAX_SIDE} px (same copy for all models) "
          "+ neutral question only")
    print(f"confirm code: {code}")
    print(f"to start the paid run after review: mapqa pilot run --confirm {code}")
    return 0


def cmd_run(args) -> int:
    data, out = _dirs(args)
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        return _fail("run", f"no manifest in {out}; run `mapqa pilot plan` first", 3)
    if not args.confirm or args.confirm != confirm_code(manifest_path):
        return _fail("run", "missing or wrong --confirm code; review `mapqa pilot plan` output and pass its code", 2)
    try:
        key = api_key_from_env()
    except ApiKeyError as exc:
        return _fail("run", str(exc), 4)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = [Item(**i) for i in manifest["items"]]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    reader = Reader(make_transport(key), cache_dir=out / "cache", log_path=out / "calls.jsonl", api_key=key,
                    budget=manifest["budget_per_model"], run_id=run_id)
    try:
        readings = reader.read_all(items, data, manifest["models"])
    except (OSError, ValueError) as exc:
        return _fail("run", f"crop problem, nothing sent: {exc}", 3)
    _write_json(out / "readings.json", [r.to_dict() for r in readings])
    print(f"run {run_id}: {len(readings)} readings -> {out / 'readings.json'}")
    print("calls made: " + ", ".join(f"{m} {reader.calls_made.get(m, 0)}" for m in manifest["models"]))
    for m in manifest["models"]:
        counts = Counter(r.status for r in readings if r.model == m)
        cached = sum(r.cached for r in readings if r.model == m)
        print(f"  {SHORT.get(m, m)}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + f"; cache hits {cached}")
    if reader.stopped_by_error:
        return _fail("run", "an API error stopped all paid calls (model_unavailable); rerun later resumes from the cache", 4)
    return 0


def _call_totals(log: Path) -> dict:
    totals: dict = {}
    if not log.is_file():
        return totals
    for line in log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        t = totals.setdefault(rec["model"], {"paid": 0, "cached": 0, "cost": 0.0})
        if rec["cached"]:
            t["cached"] += 1
        elif rec["status"] not in ("not_attempted", "budget_exhausted"):
            t["paid"] += 1
            t["cost"] += rec.get("cost_usd") or 0.0
    return totals


def cmd_evaluate(args) -> int:
    data, out = _dirs(args)
    needed = [out / "strata.json", out / "readings.json"]
    missing = [p.name for p in needed if not p.is_file()]
    if missing:
        return _fail("evaluate", f"missing {', '.join(missing)} in {out}; run `mapqa pilot plan` and `pilot run`", 3)
    try:
        labels = pd.read_csv(data / "signs" / "labels.csv", dtype=str)
        signs = pd.read_csv(data / "signs" / "signs.csv", dtype=str)
    except OSError as exc:
        return _fail("evaluate", f"labels unreadable ({exc})", 3)
    strata = json.loads((out / "strata.json").read_text(encoding="utf-8"))
    readings = json.loads((out / "readings.json").read_text(encoding="utf-8"))
    try:
        metrics = evaluate(readings, labels, signs, strata)
    except (ValueError, KeyError) as exc:
        return _fail("evaluate", str(exc), 3)
    _write_json(out / "report.json", metrics)
    (out / "report.md").write_text(render(metrics, _call_totals(out / "calls.jsonl")), encoding="utf-8", newline="\n")
    print(f"report: {out / 'report.md'}")
    for m, v in metrics["models"].items():
        a = v["accuracy_after"]
        print(f"  {SHORT[m]}: accuracy {a['correct']}/{a['n']}, coverage {v['coverage']['answered']}/{v['coverage']['n']}, "
              f"negatives with a value {v['negatives']['wrong_value']}/{v['negatives']['n']}")
    d = metrics["detector"]
    print(f"  Mapillary detector: accuracy {d['accuracy_after']['correct']}/{d['accuracy_after']['n']}, "
          f"negatives with a value {d['negatives']['wrong_value']}/{d['negatives']['n']}")
    e = metrics["chain"]["end_to_end"]
    print(f"  chain (tier 1 {SHORT[metrics['tier1_model']]}): {e['correct']}/{e['resolved']} correct, "
          f"{metrics['chain']['tiers']['human']['received']} to the manual queue")
    return 0
