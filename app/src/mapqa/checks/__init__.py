"""Stage 1 checks (ADR-002 §6)."""

from __future__ import annotations

from typing import Any

from mapqa.models import Candidate, SpeedLimit, iso


def road_candidate(row: dict[str, Any], limit: SpeedLimit, **fields: Any) -> Candidate:
    """Candidate describing one OSM road (missing_limit, unparseable_value, tag_inconsistency)."""
    return Candidate(
        road_class=row["highway"],
        osm_ids=[int(row["osm_id"])],
        geometry=row["geometry"],
        osm_value_raw=limit.raw_text(),
        osm_value_kmh=limit.value_kmh,
        osm_forward_kmh=limit.forward_kmh,
        osm_backward_kmh=limit.backward_kmh,
        osm_version=int(row["osm_version"]),
        osm_timestamp=iso(row["osm_timestamp"]),
        **fields,
    )
