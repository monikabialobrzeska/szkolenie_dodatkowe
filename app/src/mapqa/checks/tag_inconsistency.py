"""Check 3 — implicit source tag contradicts maxspeed (ADR-002 §6.4, PRD AC-17a)."""

from __future__ import annotations

import pandas as pd

from mapqa.checks import road_candidate
from mapqa.interpret import category_values
from mapqa.models import Candidate, SpeedLimit

SOURCE_TAGS = (("source_maxspeed", "source:maxspeed"), ("maxspeed_type", "maxspeed:type"))


def evaluate_tag_inconsistency(roads: pd.DataFrame, limits: dict[int, SpeedLimit]) -> tuple[list[Candidate], int]:
    """Return candidates and the number of roads with an unrecognised (non-category) source value."""
    out: list[Candidate] = []
    unrecognised = 0
    for row in roads.to_dict("records"):
        limit = limits[int(row["osm_id"])]
        values = {tag: limit.raw.get(tag) for _, tag in SOURCE_TAGS}
        present = {tag: v for tag, v in values.items() if v is not None}
        if not present:
            continue
        if any(category_values(v) is None for v in present.values()):
            unrecognised += 1
        if limit.kind != "numeric" or limit.raw.get("maxspeed") is None:
            continue
        broken = [(tag, v, category_values(v)) for tag, v in present.items()
                  if category_values(v) is not None and limit.value_kmh not in category_values(v)]
        if not broken:
            continue
        parts = [f"{tag}={value} allows {'/'.join(str(a) for a in allowed)} km/h" for tag, value, allowed in broken]
        text = f"OSM {'; '.join(parts)}, but maxspeed is {limit.value_kmh} km/h."
        diff = min(abs(limit.value_kmh - a) for _, _, allowed in broken for a in allowed)
        out.append(road_candidate(row, limit, type="tag_inconsistency", state="conflict",
                                  reason_code="implicit_source_mismatch", explanation=text, diff_kmh=diff))
    return out, unrecognised
