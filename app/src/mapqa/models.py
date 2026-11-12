"""Domain value objects shared by all modules (ADR-000 §5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CANDIDATE_TYPES = ("missing_limit", "sign_road", "unparseable_value", "tag_inconsistency")
STATES = ("conflict", "needs_review")


@dataclass(frozen=True)
class SpeedLimit:
    kind: str  # numeric | directional | missing | unparseable
    value_kmh: int | None = None
    forward_kmh: int | None = None
    backward_kmh: int | None = None
    implicit: bool = False
    converted_from_mph: bool = False
    conditional: bool = False
    variable: bool = False
    raw: dict[str, str | None] = field(default_factory=dict)

    def raw_text(self) -> str | None:
        """Compact raw representation for output columns (only present tags)."""
        present = {k: v for k, v in self.raw.items() if v is not None and k.startswith("maxspeed")}
        if not present:
            return None
        if list(present) == ["maxspeed"]:
            return present["maxspeed"]
        return "; ".join(f"{k}={v}" for k, v in present.items())


@dataclass
class Candidate:
    type: str
    state: str
    reason_code: str
    explanation: str
    road_class: str
    osm_ids: list[int]
    geometry: Any  # shapely geometry in EPSG:2180
    sign_id: str | None = None
    osm_value_raw: str | None = None
    osm_value_kmh: int | None = None
    osm_forward_kmh: int | None = None
    osm_backward_kmh: int | None = None
    sign_value_kmh: int | None = None
    diff_kmh: int | None = None
    distance_m: float | None = None
    sign_first_seen_at: str | None = None
    sign_last_seen_at: str | None = None
    osm_version: int | None = None
    osm_timestamp: str | None = None
    photo_count: int = 0
    photo_ids: list[str] = field(default_factory=list)
    candidate_id: str | None = None
    rank: int | None = None


def iso(ts: Any) -> str | None:
    """UTC ISO-8601 text for timestamps (pandas/datetime) or None."""
    if ts is None:
        return None
    try:
        import pandas as pd

        if pd.isna(ts):
            return None
        return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return str(ts)
