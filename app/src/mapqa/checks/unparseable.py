"""Check — unparseable OSM speed-limit values (ADR-002 §6.3, PRD AC-16)."""

from __future__ import annotations

import pandas as pd

from mapqa.checks import road_candidate
from mapqa.models import Candidate, SpeedLimit


def evaluate_unparseable(roads: pd.DataFrame, limits: dict[int, SpeedLimit]) -> list[Candidate]:
    out: list[Candidate] = []
    for row in roads.to_dict("records"):
        limit = limits[int(row["osm_id"])]
        if limit.kind != "unparseable":
            continue
        raw = limit.raw_text()
        out.append(road_candidate(row, limit, type="unparseable_value", state="needs_review",
                                  reason_code="unparseable_value",
                                  explanation=f"OSM speed-limit value '{raw}' cannot be interpreted."))
    return out
