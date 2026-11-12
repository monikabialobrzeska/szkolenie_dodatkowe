"""ADR-002 §9 cases 2-4 — sign vs road: direction, association, reason precedence."""

from __future__ import annotations

from datetime import datetime, timezone

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from conftest import road_row
from mapqa.checks.sign_road import RoadInfo, SignInfo, associate, evaluate_sign, parse_sign_value
from mapqa.interpret import interpret_road

OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
NEW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def road(tags: dict, *, highway="residential", bridge=None, tunnel=None, edited=OLD, osm_id=1) -> RoadInfo:
    base = {"maxspeed": None, "maxspeed_forward": None, "maxspeed_backward": None, "maxspeed_conditional": None,
            "maxspeed_variable": None, "source_maxspeed": None, "maxspeed_type": None}
    base.update(tags)
    return RoadInfo(osm_id=osm_id, highway=highway, bridge=bridge, tunnel=tunnel, osm_version=1,
                    osm_timestamp=edited, speed=interpret_road(base))


def sign(value: int, last_seen=NEW) -> SignInfo:
    return SignInfo(sign_id="s1", value_kmh=value, first_seen_at=OLD, last_seen_at=last_seen, geometry=Point(0, 0))


def test_parse_sign_value():
    assert parse_sign_value("regulatory--maximum-speed-limit-70--g1") == 70
    assert parse_sign_value("regulatory--maximum-speed-limit-5--g2") == 5
    assert parse_sign_value("regulatory--maximum-speed-limit-200--g1") is None
    assert parse_sign_value("regulatory--no-entry--g1") is None


# case 2 — directional comparison
@pytest.mark.parametrize(
    ("tags", "value", "expected_reason", "expected_diff"),
    [
        ({"maxspeed_forward": "50", "maxspeed_backward": "70"}, 70, None, None),
        ({"maxspeed_forward": "50", "maxspeed_backward": "70"}, 40, "direction_unknown", 10),
        ({"maxspeed_forward": "40"}, 50, "direction_unknown", 10),  # forward only: backward unknown never matches
        ({"maxspeed_forward": "40", "maxspeed": "50"}, 50, None, None),  # backward falls back to maxspeed
    ],
)
def test_directional_comparison(tags, value, expected_reason, expected_diff):
    candidate = evaluate_sign(sign(value), [(road(tags), 5.0)], distance_limit_m=20.0)
    if expected_reason is None:
        assert candidate is None
    else:
        assert (candidate.reason_code, candidate.diff_kmh, candidate.state) == (expected_reason, expected_diff, "needs_review")


# case 3 — association on real geometry and the 20 m boundary
def test_association_real_fixtures(fixture_signs, fixture_roads, cases):
    matches = associate(fixture_signs, fixture_roads, distance_m=20.0, excluded_classes=("service",))
    assert len(matches[cases["sign_equal"]["sign_id"]]) == 1
    assert len(matches[cases["sign_multi_two_roads"]["sign_id"]]) == 2
    assert matches.get(cases["sign_service_only"]["sign_id"], []) == []
    two = matches[cases["sign_multi_two_roads"]["sign_id"]]
    assert [d for _, d in two] == sorted(d for _, d in two)


def test_association_boundary_and_crs():
    signs = gpd.GeoDataFrame({"sign_id": ["a"]}, geometry=[Point(0, 0)], crs=2180)  # synthetic
    roads = gpd.GeoDataFrame(
        {"osm_id": [1, 2], "highway": ["residential", "residential"]},
        geometry=[LineString([(20.0, -5), (20.0, 5)]), LineString([(20.01, -5), (20.01, 5)])],
        crs=2180,
    )
    matches = associate(signs, roads, distance_m=20.0, excluded_classes=("service",))
    assert [osm_id for osm_id, _ in matches["a"]] == [1]
    with pytest.raises(ValueError, match="EPSG:2180"):
        associate(signs.to_crs(4326), roads, distance_m=20.0, excluded_classes=("service",))


def test_multiple_roads_candidate():
    candidate = evaluate_sign(sign(50), [(road({"maxspeed": "50"}, osm_id=222), 3.0),
                                         (road({"maxspeed": "50"}, osm_id=111), 8.0)], distance_limit_m=20.0)
    assert (candidate.state, candidate.reason_code) == ("needs_review", "multiple_candidate_roads")
    assert candidate.osm_ids == [222, 111]  # by distance
    assert candidate.distance_m == 3.0 and candidate.diff_kmh is None


# case 4 — precedence of uncertain reasons
@pytest.mark.parametrize(
    ("info", "value", "last_seen", "expected"),
    [
        (road({"maxspeed": "50", "maxspeed_conditional": "70 @ (Mo-Fr)"}, bridge="yes", edited=NEW), 70, OLD, "bridge_or_tunnel"),
        (road({"maxspeed": "50"}, tunnel="covered"), 70, NEW, "bridge_or_tunnel"),
        (road({"maxspeed": "50"}, tunnel="building_passage"), 70, NEW, "sign_road_mismatch"),
        (road({"maxspeed": "50"}, bridge="culvert"), 70, NEW, "sign_road_mismatch"),
        (road({"maxspeed": "140", "maxspeed_variable": "yes"}, edited=NEW), 110, OLD, "variable_limit"),
        (road({"maxspeed": "140", "maxspeed_conditional": "110 @ (Mo-Fr 06:00-18:00)"}, edited=NEW), 110, OLD, "conditional_limit"),
        (road({"maxspeed": "50"}, edited=NEW), 70, OLD, "stale_sign"),
        (road({}, edited=NEW), 70, OLD, "stale_sign"),
        (road({}, edited=OLD), 70, NEW, "road_limit_missing"),
        (road({"maxspeed": "50;70"}), 70, NEW, "unparseable_value"),
        (road({"maxspeed": "50"}, edited=OLD), 70, NEW, "sign_road_mismatch"),
    ],
)
def test_reason_precedence(info, value, last_seen, expected):
    candidate = evaluate_sign(sign(value, last_seen=last_seen), [(info, 4.0)], distance_limit_m=20.0)
    assert candidate.reason_code == expected
    assert candidate.state == "needs_review"
    assert "error" not in candidate.explanation.lower()


def test_equal_value_on_bridge_has_no_candidate():
    assert evaluate_sign(sign(50), [(road({"maxspeed": "50"}, bridge="yes"), 4.0)], distance_limit_m=20.0) is None


def test_real_stale_and_fresh_signs(fixture_signs, fixture_roads, cases):
    matches = associate(fixture_signs, fixture_roads, distance_m=20.0, excluded_classes=("service",))
    for case, expected in (("sign_differ_fresh", "sign_road_mismatch"), ("sign_differ_stale", "stale_sign")):
        sid = cases[case]["sign_id"]
        srow = fixture_signs.loc[fixture_signs["sign_id"] == sid].iloc[0]
        (osm_id, dist), = matches[sid]
        r = road_row(fixture_roads, osm_id)
        info = RoadInfo(osm_id=osm_id, highway=r["highway"], bridge=r["bridge"], tunnel=r["tunnel"],
                        osm_version=int(r["osm_version"]), osm_timestamp=r["osm_timestamp"], speed=interpret_road(r))
        s = SignInfo(sign_id=sid, value_kmh=parse_sign_value(srow["object_value"]),
                     first_seen_at=srow["first_seen_at"], last_seen_at=srow["last_seen_at"], geometry=srow.geometry)
        assert evaluate_sign(s, [(info, dist)], distance_limit_m=20.0).reason_code == expected
