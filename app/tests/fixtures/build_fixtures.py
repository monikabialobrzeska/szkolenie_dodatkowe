"""Extract small, real fixture slices from the pinned map-qa snapshot.

Read-only on the inputs. Writes roads.geojson, signs.geojson and cases.json next
to this file. Run from app/:  uv run python tests/fixtures/build_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import geopandas as gpd
import shapely

HERE = Path(__file__).resolve().parent
DATA = HERE.parents[2] / "course-materials" / "exercise-data" / "map-qa"
TAG_KEYS = [
    "maxspeed:forward",
    "maxspeed:backward",
    "maxspeed:conditional",
    "maxspeed:variable",
    "source:maxspeed",
    "maxspeed:type",
]

ROAD_CASES = {
    "numeric": "maxspeed = '50' AND fwd IS NULL AND bwd IS NULL AND src IS NULL AND typ IS NULL AND cond IS NULL AND highway = 'residential'",
    "directional_both": "maxspeed IS NULL AND fwd IS NOT NULL AND bwd IS NOT NULL AND fwd <> bwd AND fwd NOT LIKE 'PL:%' AND bwd NOT LIKE 'PL:%'",
    "forward_only": "maxspeed IS NULL AND fwd IS NOT NULL AND bwd IS NULL",
    "implicit_in_backward": "osm_id = 907138970",
    "conditional_motorway": "cond IS NOT NULL",
    "variable_motorway": "var = 'yes'",
    "main_no_limit": "highway = 'secondary' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL",
    "main_link_no_limit": "highway = 'motorway_link' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL",
    "tertiary_no_limit": "highway = 'tertiary' AND maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL",
    "bridge_yes": "bridge = 'yes' AND maxspeed IS NOT NULL",
    "building_passage": "tunnel = 'building_passage' AND maxspeed IS NOT NULL",
    "urban_70": "src = 'PL:urban' AND maxspeed = '70'",
    "rural_100": "src = 'PL:rural' AND maxspeed = '100'",
    "zone40_30": "src = 'PL:zone40' AND maxspeed = '30'",
    "source_sign": "src = 'sign' AND maxspeed IS NOT NULL",
    "type_rural_50": "typ = 'PL:rural' AND maxspeed = '50'",
}

ROADS_SQL = """
CREATE TEMP TABLE r AS
SELECT osm_id, osm_version, osm_timestamp, highway, maxspeed, oneway, bridge, tunnel, layer, name, tags,
       json_extract_string(tags, '$."maxspeed:forward"') AS fwd,
       json_extract_string(tags, '$."maxspeed:backward"') AS bwd,
       json_extract_string(tags, '$."maxspeed:conditional"') AS cond,
       json_extract_string(tags, '$."maxspeed:variable"') AS var,
       json_extract_string(tags, '$."source:maxspeed"') AS src,
       json_extract_string(tags, '$."maxspeed:type"') AS typ,
       ST_AsWKB(geometry) AS wkb,
       ST_Transform(geometry, 'EPSG:4326', 'EPSG:2180', always_xy := true) AS g
FROM read_parquet(?)
"""

SIGNS_SQL = """
CREATE TEMP TABLE s AS
SELECT id, object_value, object_type, first_seen_at, last_seen_at, source, license, source_url, image_ids,
       CAST(regexp_extract(object_value, 'limit-(\\d+)--', 1) AS INT) AS v,
       ST_AsWKB(geom) AS wkb,
       ST_Transform(geom, 'EPSG:4326', 'EPSG:2180', always_xy := true) AS g
FROM ST_Read(?)
"""

ASSOC_SQL = """
CREATE TEMP TABLE a AS
SELECT s.id, s.v, s.last_seen_at, r.osm_id, r.highway, r.maxspeed, r.fwd, r.bwd, r.cond, r.var, r.bridge, r.tunnel,
       CAST(r.osm_timestamp AS TIMESTAMPTZ) AS edited, ST_Distance(s.g, r.g) AS d
FROM s JOIN r ON ST_DWithin(s.g, r.g, 20);
CREATE TEMP TABLE elig AS SELECT * FROM a WHERE highway <> 'service';
CREATE TEMP TABLE one AS SELECT * FROM elig WHERE id IN (SELECT id FROM elig GROUP BY id HAVING count(*) = 1);
"""

SIGN_CASES = {
    "sign_equal": "SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) = v AND fwd IS NULL AND bwd IS NULL",
    "sign_differ_fresh": "SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) <> v AND fwd IS NULL AND bwd IS NULL AND cond IS NULL AND var IS NULL AND coalesce(bridge,'no') = 'no' AND coalesce(tunnel,'no') = 'no' AND last_seen_at >= edited",
    "sign_differ_stale": "SELECT id FROM one WHERE TRY_CAST(maxspeed AS INT) <> v AND fwd IS NULL AND bwd IS NULL AND cond IS NULL AND var IS NULL AND coalesce(bridge,'no') = 'no' AND coalesce(tunnel,'no') = 'no' AND last_seen_at < edited",
    "sign_multi_two_roads": "SELECT id FROM elig GROUP BY id HAVING count(*) = 2",
    "sign_service_only": "SELECT id FROM a GROUP BY id HAVING count(*) FILTER (WHERE highway <> 'service') = 0",
    "sign_directional": "SELECT id FROM one WHERE fwd IS NOT NULL OR bwd IS NOT NULL",
    "sign_road_no_limit": "SELECT id FROM one WHERE maxspeed IS NULL AND fwd IS NULL AND bwd IS NULL AND cond IS NULL",
}


def main() -> None:
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.execute(ROADS_SQL, [str(DATA / "osm_roads.parquet")])
    con.execute(SIGNS_SQL, [str(DATA / "traffic_signs.geojson")])
    con.execute(ASSOC_SQL)

    cases: dict[str, dict] = {}
    road_ids: set[int] = set()
    for name, where in ROAD_CASES.items():
        row = con.execute(f"SELECT min(osm_id) FROM r WHERE {where}").fetchone()
        if row[0] is None:
            raise SystemExit(f"no road for case {name}")
        cases[name] = {"osm_id": int(row[0]), "query": where}
        road_ids.add(int(row[0]))

    sign_ids: set[str] = set()
    for name, sql in SIGN_CASES.items():
        row = con.execute(f"SELECT min(id) FROM ({sql})").fetchone()
        if row[0] is None:
            raise SystemExit(f"no sign for case {name}")
        cases[name] = {"sign_id": row[0], "query": sql}
        sign_ids.add(row[0])

    # every road within 30 m of a selected sign, so association can be tested on real geometry
    near = con.execute(
        "SELECT DISTINCT r.osm_id FROM s JOIN r ON ST_DWithin(s.g, r.g, 30) WHERE list_contains(?, s.id)",
        [sorted(sign_ids)],
    ).fetchall()
    road_ids |= {int(x[0]) for x in near}

    roads = con.execute(
        "SELECT osm_id, osm_version, osm_timestamp, highway, maxspeed, oneway, bridge, tunnel, layer, name, tags, wkb "
        "FROM r WHERE list_contains(?, osm_id) ORDER BY osm_id",
        [sorted(road_ids)],
    ).df()
    roads_gdf = gpd.GeoDataFrame(
        roads.drop(columns="wkb"), geometry=shapely.from_wkb(roads["wkb"].map(bytes)), crs="EPSG:4326"
    )
    roads_gdf.to_file(HERE / "roads.geojson", driver="GeoJSON")

    signs = con.execute(
        "SELECT id, object_value, object_type, strftime(first_seen_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S+0000') AS first_seen_at, "
        "strftime(last_seen_at AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S+0000') AS last_seen_at, source, license, source_url, image_ids, wkb "
        "FROM s WHERE list_contains(?, id) ORDER BY id",
        [sorted(sign_ids)],
    ).df()
    signs["image_ids"] = signs["image_ids"].map(list)
    signs_gdf = gpd.GeoDataFrame(
        signs.drop(columns="wkb"), geometry=shapely.from_wkb(signs["wkb"].map(bytes)), crs="EPSG:4326"
    )
    signs_gdf.to_file(HERE / "signs.geojson", driver="GeoJSON")

    (HERE / "cases.json").write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"roads: {len(roads_gdf)}, signs: {len(signs_gdf)}, cases: {len(cases)}")


if __name__ == "__main__":
    main()
