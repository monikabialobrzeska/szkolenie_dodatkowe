"""ADR-002 §9 case 1 — interpretation of OSM speed-limit tags."""

from __future__ import annotations

import pytest

from conftest import road_row
from mapqa.interpret import interpret_road, parse_value


@pytest.mark.parametrize(
    ("raw", "kmh", "status", "implicit", "converted"),
    [
        (None, None, "absent", False, False),
        ("50", 50, "ok", False, False),
        (" 50 ", 50, "ok", False, False),
        ("30 mph", 48, "ok", False, True),  # synthetic: no mph in the snapshot
        ("30mph", 48, "ok", False, True),  # synthetic
        ("PL:urban", 50, "ok", True, False),
        ("PL:rural", 90, "ok", True, False),
        ("PL:motorway", 140, "ok", True, False),
        ("PL:living_street", 20, "ok", True, False),
        ("PL:zone30", 30, "ok", True, False),
        ("PL:expressway", None, "unparseable", False, False),  # synthetic
        ("50;70", None, "unparseable", False, False),  # synthetic
        ("none", None, "unparseable", False, False),  # synthetic
        ("50 km/h", None, "unparseable", False, False),  # synthetic
        ("0", None, "unparseable", False, False),
        ("250", None, "unparseable", False, False),
    ],
)
def test_parse_value(raw, kmh, status, implicit, converted):
    result = parse_value(raw)
    assert (result.kmh, result.status, result.implicit, result.converted) == (kmh, status, implicit, converted)


def test_interpret_real_roads(fixture_roads, cases):
    numeric = interpret_road(road_row(fixture_roads, cases["numeric"]["osm_id"]))
    assert (numeric.kind, numeric.value_kmh) == ("numeric", 50)

    both = road_row(fixture_roads, cases["directional_both"]["osm_id"])
    directional = interpret_road(both)
    assert directional.kind == "directional"
    assert directional.forward_kmh == int(both["maxspeed_forward"])
    assert directional.backward_kmh == int(both["maxspeed_backward"])

    forward_only = interpret_road(road_row(fixture_roads, cases["forward_only"]["osm_id"]))
    assert forward_only.kind == "directional"
    assert forward_only.forward_kmh is not None
    assert forward_only.backward_kmh is None  # unknown, never "no limit"

    implicit = interpret_road(road_row(fixture_roads, cases["implicit_in_backward"]["osm_id"]))
    assert implicit.kind == "directional"
    assert implicit.backward_kmh == 50 and implicit.implicit

    conditional = interpret_road(road_row(fixture_roads, cases["conditional_motorway"]["osm_id"]))
    assert conditional.conditional and conditional.kind == "numeric"

    variable = interpret_road(road_row(fixture_roads, cases["variable_motorway"]["osm_id"]))
    assert variable.variable

    missing = interpret_road(road_row(fixture_roads, cases["main_no_limit"]["osm_id"]))
    assert missing.kind == "missing" and missing.value_kmh is None


def test_unparseable_directional_and_raw_preserved():
    row = {"maxspeed": None, "maxspeed_forward": "50;60", "maxspeed_backward": "40", "maxspeed_conditional": None,
           "maxspeed_variable": None, "source_maxspeed": "sign", "maxspeed_type": None}  # synthetic
    result = interpret_road(row)
    assert result.kind == "unparseable"
    assert result.raw == {"maxspeed": None, "maxspeed:forward": "50;60", "maxspeed:backward": "40",
                          "maxspeed:conditional": None, "maxspeed:variable": None,
                          "source:maxspeed": "sign", "maxspeed:type": None}
