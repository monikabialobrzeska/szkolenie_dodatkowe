"""ADR-002 §9 case 6 — ranking and stable identifiers."""

from __future__ import annotations

import random

from shapely.geometry import Point

from mapqa.models import Candidate
from mapqa.ranking import candidate_id, rank_candidates


def cand(type_, osm_ids, road_class, diff=None, sign_id=None):
    return Candidate(type=type_, state="needs_review", reason_code="x", explanation="x", road_class=road_class,
                     osm_ids=osm_ids, sign_id=sign_id, diff_kmh=diff, geometry=Point(0, 0))


def test_candidate_ids():
    assert candidate_id(cand("missing_limit", [123], "secondary")) == "missing_limit:w123"
    assert candidate_id(cand("tag_inconsistency", [5], "primary")) == "tag_inconsistency:w5"
    assert candidate_id(cand("sign_road", [77], "primary", sign_id="637")) == "sign_road:mly637:w77"
    assert candidate_id(cand("sign_road", [222, 111], "primary", sign_id="9")) == "sign_road:mly9:w111+w222"


def test_order_is_stable_and_follows_class_then_diff():
    items = [
        cand("sign_road", [1], "residential", diff=30, sign_id="a"),
        cand("missing_limit", [2], "secondary"),
        cand("sign_road", [3], "motorway_link", diff=10, sign_id="b"),
        cand("sign_road", [4], "motorway", diff=40, sign_id="c"),
        cand("missing_limit", [5], "motorway"),
        cand("tag_inconsistency", [6], "trunk_link", diff=20),
    ]
    expected = ["sign_road:mlyc:w4", "sign_road:mlyb:w3", "missing_limit:w5", "tag_inconsistency:w6",
                "missing_limit:w2", "sign_road:mlya:w1"]
    for seed in range(5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        ranked = rank_candidates(shuffled)
        assert [c.candidate_id for c in ranked] == expected
        assert [c.rank for c in ranked] == [1, 2, 3, 4, 5, 6]
