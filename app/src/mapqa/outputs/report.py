"""summary.md for the team lead (ADR-000 §6.2, PRD §9.1, AC-26, AC-27)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from mapqa.interpret import ALLOWED_VALUES, IMPLICIT_VALUES
from mapqa.outputs import ATTRIBUTION


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return lines


def render_summary(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    snap = summary["snapshot"]
    src = summary["source"]
    counts = summary["counts"]
    out = [
        "# Speed-limit candidates — Kraków (Stage 1)",
        "",
        f"Run at {summary['run_at']} · tool {summary['tool_version']} · source `{src['kind']}` ({src['location']}), "
        f"read at {src['read_at']}.",
        "",
        f"Snapshot: OSM extract {snap['osm_extract_date']} · Overture release {snap['overture_release']} · "
        f"snapshot created {snap['created_at']}.",
        "",
        "The tool lists **candidates** for human review. It never states that the map is wrong.",
        "",
        "## Rows read",
        "",
        *_table(["Dataset", "Rows"], [[k, v["rows"]] for k, v in summary["inputs"].items() if k != "snapshot"]),
        "",
        "## Candidates by type and state",
        "",
    ]
    by_ts = Counter((r["type"], r["state"]) for r in records)
    types = sorted({t for t, _ in by_ts})
    rows = [[t, by_ts[(t, "conflict")], by_ts[(t, "needs_review")], by_ts[(t, "conflict")] + by_ts[(t, "needs_review")]]
            for t in types]
    rows.append(["**total**", sum(by_ts[(t, "conflict")] for t in types),
                 sum(by_ts[(t, "needs_review")] for t in types), len(records)])
    out += _table(["Type", "conflict", "needs_review", "Total"], rows)

    out += ["", "## Candidates by reason", ""]
    reasons = Counter((r["type"], r["reason_code"]) for r in records)
    out += _table(["Type", "Reason", "Candidates"], [[t, rc, n] for (t, rc), n in sorted(reasons.items())])

    out += ["", "## Candidates by road class", ""]
    classes = Counter(r["road_class"] for r in records)
    out += _table(["Road class", "Candidates"], [[k, v] for k, v in sorted(classes.items(), key=lambda kv: (-kv[1], kv[0]))])

    out += ["", "## Skipped and unmatched records", ""]
    out += _table(["Reason", "Records"], [[k, v] for k, v in sorted(counts["skipped"].items())])
    no_photos = sum(1 for r in records if not r["photo_count"])
    out += ["", f"Candidates with no photos linked: {no_photos} of {len(records)}."]

    out += ["", "## Settings", "",
            f"- Sign-to-road distance limit: {summary['config']['sign_road_distance_m']:g} m "
            f"(roads of class {', '.join(summary['config']['association_excluded_classes'])} ignored).",
            f"- Main road classes for *missing limit*: {', '.join(summary['config']['main_road_classes'])}.",
            "", "### Implicit Polish speed-limit categories", ""]
    impl = [["`" + k + "`", "unparseable" if v is None else v, "/".join(map(str, ALLOWED_VALUES[k]))]
            for k, v in IMPLICIT_VALUES.items()]
    impl.append(["`PL:zone<N>`", "N", "N"])
    out += _table(["Category", "Interpreted as (km/h)", "Allowed with maxspeed (km/h)"], impl)
    out += ["", "Mapillary sign detections are a working reference, not ground truth.", "",
            "## Attribution and licences", "", *[f"- {line}" for line in ATTRIBUTION],
            "- Details: `course-materials/exercise-data/map-qa/ATTRIBUTION.md`", ""]
    return "\n".join(out)


def write_summary(path: Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    path.write_text(render_summary(summary, records), encoding="utf-8", newline="\n")
