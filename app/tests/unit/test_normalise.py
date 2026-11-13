"""ADR-004 §3 — shared normalisers make both sources structurally identical (parity at row level)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import duckdb
import pandas as pd
from pandas.testing import assert_frame_equal

from conftest import DATA_DIR
from mapqa.sources.databricks import ROADS_COLUMNS, SIGNS_COLUMNS
from mapqa.sources.databricks_client import DatabricksClient
from mapqa.sources.normalise import roads_frame, signs_frame

CAPTURES = Path(__file__).resolve().parents[1] / "fixtures" / "databricks"


def replayed_records(name: str) -> list[dict]:
    capture = json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))
    exchanges = list(capture["exchanges"])

    def transport(method, url, body, headers):
        e = exchanges.pop(0)
        return e["http_status"], "application/json", json.dumps(e["body"])

    result = DatabricksClient("https://x", "t", "w", transport=transport, sleep=lambda s: None).execute(
        capture["request"]["statement"], step=name)
    records = result.records()
    for r in records:
        r["geometry_wkb"] = base64.b64decode(r.pop("geometry_b64"))
    return records


def local(sql: str, params: list) -> pd.DataFrame:
    con = duckdb.connect()
    con.execute("LOAD spatial")
    return con.execute(sql, params).to_arrow_table().to_pandas()


def test_roads_identical_from_files_and_databricks():
    remote = roads_frame(pd.DataFrame(replayed_records("osm_projection_sample")))
    ids = [int(i) for i in remote["osm_id"]]
    tags = ", ".join(f"json_extract_string(tags, '$.\"{k}\"') AS {c}" for c, k in (
        ("maxspeed_forward", "maxspeed:forward"), ("maxspeed_backward", "maxspeed:backward"),
        ("maxspeed_conditional", "maxspeed:conditional"), ("maxspeed_variable", "maxspeed:variable"),
        ("source_maxspeed", "source:maxspeed"), ("maxspeed_type", "maxspeed:type")))
    files = local(f"SELECT {', '.join(c for c in ROADS_COLUMNS)}, {tags}, ST_AsWKB(geometry) AS geometry_wkb "
                  "FROM read_parquet(?) WHERE list_contains(?, osm_id)", [str(DATA_DIR / "osm_roads.parquet"), ids])
    files["geometry_wkb"] = files["geometry_wkb"].map(bytes)
    local_frame = roads_frame(files)
    assert_frame_equal(local_frame.drop(columns="geometry"), remote.drop(columns="geometry"))
    assert local_frame.crs.to_epsg() == remote.crs.to_epsg() == 2180
    assert (local_frame.geometry.to_wkb() == remote.geometry.to_wkb()).all()


def test_signs_identical_from_files_and_databricks():
    remote, outside_r = signs_frame(pd.DataFrame(replayed_records("signs_sample")), bbox=(19.80, 49.95, 20.15, 50.15))
    ids = list(remote["sign_id"])
    files = local(f"SELECT {', '.join(SIGNS_COLUMNS)}, ST_AsWKB(geom) AS geometry_wkb FROM ST_Read(?) "
                  "WHERE list_contains(?, id)", [str(DATA_DIR / "traffic_signs.geojson"), ids])
    files["geometry_wkb"] = files["geometry_wkb"].map(bytes)
    local_frame, outside_l = signs_frame(files, bbox=(19.80, 49.95, 20.15, 50.15))
    assert outside_r == outside_l == 0
    assert_frame_equal(local_frame.drop(columns="geometry"), remote.drop(columns="geometry"))
    assert (local_frame.geometry.to_wkb() == remote.geometry.to_wkb()).all()
    assert isinstance(remote["image_ids"].iloc[0], list)
