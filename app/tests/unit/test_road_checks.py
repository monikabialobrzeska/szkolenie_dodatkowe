"""ADR-002 §9 case 5 — road-level checks: missing limit, unparseable value, tag inconsistency."""

from __future__ import annotations

from mapqa.checks.missing_limit import evaluate_missing_limit
from mapqa.checks.tag_inconsistency import evaluate_tag_inconsistency
from mapqa.checks.unparseable import evaluate_unparseable
from mapqa.interpret import interpret_road

MAIN = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link")


def _pick(roads, cases, *names):
    ids = [cases[n]["osm_id"] for n in names]
    return roads[roads["osm_id"].isin(ids)].reset_index(drop=True)


def _limits(roads):
    return {int(r["osm_id"]): interpret_road(r) for r in roads.to_dict("records")}


def test_missing_limit(fixture_roads, cases):
    roads = _pick(fixture_roads, cases, "main_no_limit", "main_link_no_limit", "tertiary_no_limit",
                  "directional_both", "numeric", "variable_motorway")
    found = {c.osm_ids[0]: c for c in evaluate_missing_limit(roads, _limits(roads), MAIN)}
    assert set(found) == {cases["main_no_limit"]["osm_id"], cases["main_link_no_limit"]["osm_id"]}
    for c in found.values():
        assert (c.type, c.state, c.reason_code) == ("missing_limit", "conflict", "missing_limit")


def test_unparseable_value(fixture_roads, cases):
    roads = _pick(fixture_roads, cases, "numeric").copy()
    assert evaluate_unparseable(roads, _limits(roads)) == []
    roads.loc[0, "maxspeed"] = "50;70"  # synthetic: not present in the snapshot
    (c,) = evaluate_unparseable(roads, _limits(roads))
    assert (c.type, c.state, c.reason_code, c.osm_value_raw) == ("unparseable_value", "needs_review", "unparseable_value", "50;70")


def test_tag_inconsistency(fixture_roads, cases):
    roads = _pick(fixture_roads, cases, "urban_70", "rural_100", "zone40_30", "source_sign", "type_rural_50", "numeric")
    candidates, unrecognised = evaluate_tag_inconsistency(roads, _limits(roads))
    found = {c.osm_ids[0]: c for c in candidates}
    assert set(found) == {cases["urban_70"]["osm_id"], cases["zone40_30"]["osm_id"], cases["type_rural_50"]["osm_id"]}
    urban = found[cases["urban_70"]["osm_id"]]
    assert (urban.type, urban.state, urban.reason_code) == ("tag_inconsistency", "conflict", "implicit_source_mismatch")
    assert "PL:urban" in urban.explanation and "70" in urban.explanation
    assert unrecognised == 1  # source:maxspeed=sign
