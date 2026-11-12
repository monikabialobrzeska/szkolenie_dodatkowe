"""Check 1 — missing limit on main roads (ADR-002 §6.1, PRD AC-15)."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from mapqa.checks import road_candidate
from mapqa.models import Candidate, SpeedLimit


def evaluate_missing_limit(roads: pd.DataFrame, limits: dict[int, SpeedLimit], main_classes: Iterable[str]) -> list[Candidate]:
    main = set(main_classes)
    out: list[Candidate] = []
    for row in roads[roads["highway"].isin(main)].to_dict("records"):
        limit = limits[int(row["osm_id"])]
        if limit.kind != "missing":
            continue
        if limit.variable:
            state, reason, text = "needs_review", "variable_limit", "Road has a variable speed limit (maxspeed:variable)."
        elif limit.conditional:
            state, reason, text = "needs_review", "conditional_limit", "Road has a conditional speed limit (maxspeed:conditional)."
        else:
            state, reason, text = "conflict", "missing_limit", f"Main road ({row['highway']}) has no speed-limit value in OSM."
        out.append(road_candidate(row, limit, type="missing_limit", state=state, reason_code=reason, explanation=text))
    return out
