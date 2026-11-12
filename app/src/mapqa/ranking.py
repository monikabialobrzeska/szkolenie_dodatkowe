"""Stable identifiers and ordering (ADR-002 §7.2-§7.3, PRD AC-19, AC-20)."""

from __future__ import annotations

from typing import Iterable

from mapqa.models import Candidate

CLASS_RANK = {
    "motorway": 0, "motorway_link": 0,
    "trunk": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4,
}


def candidate_id(candidate: Candidate) -> str:
    if candidate.type == "sign_road":
        ways = "+".join(f"w{osm_id}" for osm_id in sorted(candidate.osm_ids))
        return f"sign_road:mly{candidate.sign_id}:{ways}"
    return f"{candidate.type}:w{candidate.osm_ids[0]}"


def _key(candidate: Candidate) -> tuple:
    diff = candidate.diff_kmh
    return (CLASS_RANK.get(candidate.road_class, 5), diff is None, -(diff or 0), candidate.candidate_id)


def rank_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    items = list(candidates)
    for c in items:
        c.candidate_id = candidate_id(c)
    items.sort(key=_key)
    for position, c in enumerate(items, start=1):
        c.rank = position
    return items
