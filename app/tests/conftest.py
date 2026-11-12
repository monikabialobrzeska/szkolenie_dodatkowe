from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "course-materials" / "exercise-data" / "map-qa"

TAG_COLUMNS = {
    "maxspeed:forward": "maxspeed_forward",
    "maxspeed:backward": "maxspeed_backward",
    "maxspeed:conditional": "maxspeed_conditional",
    "maxspeed:variable": "maxspeed_variable",
    "source:maxspeed": "source_maxspeed",
    "maxspeed:type": "maxspeed_type",
}


@pytest.fixture(scope="session")
def cases() -> dict:
    return json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixture_roads() -> gpd.GeoDataFrame:
    """Real OSM rows, normalised like the files adapter does (tag columns, EPSG:2180)."""
    roads = gpd.read_file(FIXTURES / "roads.geojson")
    # pyogrio may already decode the JSON string into a dict; keep `tags` as the raw JSON text
    roads["tags"] = roads["tags"].map(lambda t: t if isinstance(t, str) else json.dumps(t, ensure_ascii=False, sort_keys=True))
    for tag, column in TAG_COLUMNS.items():
        roads[column] = roads["tags"].map(lambda t, k=tag: json.loads(t).get(k))
    roads["osm_id"] = roads["osm_id"].astype("int64")
    roads["osm_timestamp"] = pd.to_datetime(roads["osm_timestamp"], utc=True)
    return roads.to_crs(2180).sort_values("osm_id").reset_index(drop=True)


@pytest.fixture(scope="session")
def fixture_signs() -> gpd.GeoDataFrame:
    signs = gpd.read_file(FIXTURES / "signs.geojson")
    signs = signs.rename(columns={"id": "sign_id"})
    for column in ("first_seen_at", "last_seen_at"):
        signs[column] = pd.to_datetime(signs[column], utc=True)
    return signs.to_crs(2180).sort_values("sign_id").reset_index(drop=True)


def road_row(roads: gpd.GeoDataFrame, osm_id: int) -> dict:
    return roads.loc[roads["osm_id"] == osm_id].iloc[0].to_dict()
