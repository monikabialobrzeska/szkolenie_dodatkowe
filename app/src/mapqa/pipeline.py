"""Runs the Stage 1 pipeline and writes outputs atomically (ADR-000 §4, D5, D6)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mapqa import __version__
from mapqa.checks.missing_limit import evaluate_missing_limit
from mapqa.checks.sign_road import evaluate_signs
from mapqa.checks.tag_inconsistency import evaluate_tag_inconsistency
from mapqa.checks.unparseable import evaluate_unparseable
from mapqa.config import RunConfig
from mapqa.evidence import attach_photos
from mapqa.interpret import interpret_road
from mapqa.outputs.queue import to_records, write_csv, write_geojson
from mapqa.outputs.report import write_summary
from mapqa.outputs.viewer import build_viewer_data, write_viewer
from mapqa.ranking import rank_candidates
from mapqa.sources.base import InputError, adapter_for
from mapqa.sources.files import FILES

OUTPUT_FILES = ("review_queue.csv", "review_queue.geojson", "summary.md", "run_summary.json", "viewer.html")


class InputsChanged(Exception):
    """Input checksums differ before and after the run (a bug, exit code 1)."""


@dataclass
class RunResult:
    summary: dict[str, Any]
    output_dir: Path
    candidates: int = 0
    outputs: dict[str, str] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(config: RunConfig, command: str = "run") -> RunResult:
    adapter = adapter_for(config)
    run_at = _now()
    failed = [s for s in adapter.check() if not s.ok]
    if failed:
        raise InputError(failed[0].problem)  # names the missing or unreadable input
    before = adapter.checksums()
    bundle = adapter.load()

    roads = bundle.roads
    limits = {int(r["osm_id"]): interpret_road(r) for r in roads.drop(columns="geometry").to_dict("records")}
    candidates = evaluate_missing_limit(roads, limits, config.main_road_classes)
    sign_candidates, sign_counts = evaluate_signs(bundle.signs, roads, limits, config.sign_road_distance_m,
                                                  config.association_excluded_classes)
    candidates += sign_candidates
    candidates += evaluate_unparseable(roads, limits)
    tag_candidates, unrecognised = evaluate_tag_inconsistency(roads, limits)
    candidates += tag_candidates
    attach_photos(candidates, bundle.photo_links)
    ranked = rank_candidates(candidates)
    records = to_records(ranked)

    skipped = dict(bundle.skipped)
    skipped.update(sign_counts)
    skipped["implicit_source_unrecognised"] = unrecognised
    skipped["photos_without_osm_link"] = int(bundle.photo_links["osm_way_id"].isna().sum())

    summary: dict[str, Any] = {
        "tool_version": __version__,
        "command": command,
        "exit_code": 0,
        "run_at": run_at,
        "source": {"kind": adapter.kind, "location": adapter.location(), "read_at": bundle.read_at},
        "snapshot": bundle.snapshot,
        "config": config.public_dict(),
        "inputs": {},
        "counts": {
            "by_type_state": dict(sorted(Counter(f"{r['type']}|{r['state']}" for r in records).items())),
            "by_reason": dict(sorted(Counter(f"{r['type']}|{r['reason_code']}" for r in records).items())),
            "by_road_class": dict(sorted(Counter(r["road_class"] for r in records).items())),
            "skipped": dict(sorted(skipped.items())),
            "candidates": len(records),
        },
        "outputs": {name: name for name in OUTPUT_FILES},
    }
    out_dir = config.output_dir
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        write_csv(tmp / "review_queue.csv", records)
        write_geojson(tmp / "review_queue.geojson", records)
        after = adapter.checksums()
        for name in FILES:
            summary["inputs"][name] = {
                "path": str(config.data_dir / FILES[name]),
                "rows": bundle.row_counts.get(name),
                "sha256_before": before[name],
                "sha256_after": after[name],
            }
        if before != after:
            changed = ", ".join(n for n in before if before[n] != after[n])
            raise InputsChanged(f"input changed during run: {changed}")
        write_summary(tmp / "summary.md", summary, records)
        (tmp / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8", newline="\n")
        write_viewer(tmp / "viewer.html", build_viewer_data(summary, records, bundle.photos, config.data_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in OUTPUT_FILES:
            os.replace(tmp / name, out_dir / name)
        stale = out_dir / "last_failed_run.json"
        if stale.exists():
            stale.unlink()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return RunResult(summary=summary, output_dir=out_dir, candidates=len(records),
                     outputs={name: str(out_dir / name) for name in OUTPUT_FILES})


def write_failure(config: RunConfig, command: str, exit_code: int, step: str, message: str) -> None:
    """Record a failed run without touching existing queue files (AC-02)."""
    try:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        payload = {"tool_version": __version__, "command": command, "exit_code": exit_code, "run_at": _now(),
                   "step": step, "message": message, "config": config.public_dict()}
        (config.output_dir / "last_failed_run.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
