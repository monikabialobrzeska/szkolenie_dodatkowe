"""Photo evidence per candidate (ADR-002 §7.1). Headings are never read (AC-18)."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import pandas as pd

from mapqa.models import Candidate


def attach_photos(candidates: Iterable[Candidate], links: pd.DataFrame) -> None:
    by_way: dict[int, list[tuple[float, str]]] = defaultdict(list)
    linked = links.dropna(subset=["osm_way_id"])
    for row in linked[["osm_way_id", "distance_m", "photo_id"]].itertuples(index=False):
        by_way[int(row.osm_way_id)].append((float(row.distance_m), str(row.photo_id)))
    for candidate in candidates:
        best: dict[str, float] = {}
        for osm_id in candidate.osm_ids:
            for distance, photo_id in by_way.get(osm_id, []):
                if photo_id not in best or distance < best[photo_id]:
                    best[photo_id] = distance
        ordered = sorted(best.items(), key=lambda item: (item[1], item[0]))
        candidate.photo_ids = [photo_id for photo_id, _ in ordered]
        candidate.photo_count = len(ordered)
