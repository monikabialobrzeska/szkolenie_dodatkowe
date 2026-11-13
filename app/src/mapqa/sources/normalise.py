"""Shared normalisers: both adapters end here, so InputBundle dtypes are identical by construction (ADR-004 §3).

Input: plain columns (any of str / numbers / timestamps) plus `geometry_wkb` bytes in EPSG:4326 where relevant.
Output: fixed dtypes, UTC timestamps in microseconds, text as object with None, geometries in EPSG:2180, sorted.
"""

from __future__ import annotations

from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
import shapely

TAG_COLUMNS = ["maxspeed_forward", "maxspeed_backward", "maxspeed_conditional", "maxspeed_variable",
               "source_maxspeed", "maxspeed_type"]
ROAD_TEXT = ["highway", "maxspeed", "oneway", "bridge", "tunnel", "layer", "name", *TAG_COLUMNS]


def _text(values: Iterable[Any]) -> pd.Series:
    return pd.Series([None if v is None or (not isinstance(v, (list, tuple)) and pd.isna(v)) else str(v) for v in values],
                     dtype=object)


def _utc(values: Iterable[Any]) -> pd.Series:
    return pd.to_datetime(pd.Series(list(values)), utc=True, format="ISO8601").dt.as_unit("us")


def _wkb(frame: pd.DataFrame) -> Any:
    return shapely.from_wkb([bytes(b) for b in frame["geometry_wkb"]])


def roads_frame(df: pd.DataFrame) -> gpd.GeoDataFrame:
    df = df.reset_index(drop=True)
    out = pd.DataFrame({
        "osm_id": df["osm_id"].astype("int64"),
        "osm_version": df["osm_version"].astype("int64"),
        "osm_timestamp": _utc(df["osm_timestamp"]),
    })
    for column in ROAD_TEXT:
        out[column] = _text(df[column])
    roads = gpd.GeoDataFrame(out, geometry=_wkb(df), crs="EPSG:4326").to_crs(2180)
    return roads.sort_values("osm_id").reset_index(drop=True)


def signs_frame(df: pd.DataFrame, bbox: tuple[float, float, float, float]) -> tuple[gpd.GeoDataFrame, int]:
    """Returns signs inside the bbox (EPSG:2180) and the number of signs outside it."""
    df = df.reset_index(drop=True)
    out = pd.DataFrame({
        "sign_id": _text(df["id"]),
        "object_value": _text(df["object_value"]),
        "first_seen_at": _utc(df["first_seen_at"]),
        "last_seen_at": _utc(df["last_seen_at"]),
        "image_ids": pd.Series([[str(x) for x in v] if v is not None else [] for v in df["image_ids"]], dtype=object),
        "source_url": _text(df["source_url"]),
    })
    signs = gpd.GeoDataFrame(out, geometry=_wkb(df), crs="EPSG:4326")
    min_lon, min_lat, max_lon, max_lat = bbox
    inside = signs.geometry.x.between(min_lon, max_lon) & signs.geometry.y.between(min_lat, max_lat)
    outside = int((~inside).sum())
    signs = signs[inside].to_crs(2180)
    return signs.sort_values("sign_id").reset_index(drop=True), outside


def photos_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    out = pd.DataFrame({c: _text(df[c]) for c in ("photo_id", "filename", "source", "source_url", "author", "license")})
    out["captured_at"] = _utc(df["captured_at"])
    for column in ("longitude", "latitude", "heading"):
        out[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")
    return out.sort_values("photo_id").reset_index(drop=True)


def links_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.reset_index(drop=True)
    out = pd.DataFrame({
        "photo_id": _text(df["photo_id"]),
        "osm_way_id": pd.array([None if pd.isna(v) else int(v) for v in df["osm_way_id"]], dtype="Int64"),
        "overture_id": _text(df["overture_id"]),
        "distance_m": pd.to_numeric(df["distance_m"], errors="coerce").astype("float64"),
        "candidate_flags": _text(df["candidate_flags"]),
    })
    return out.sort_values(["photo_id", "osm_way_id"]).reset_index(drop=True)


def overture_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"id": _text(df["id"]), "class": _text(df["class"])})
    return out.sort_values("id").reset_index(drop=True)
