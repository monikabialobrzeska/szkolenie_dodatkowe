"""viewer.html — read-only result viewer (ADR-003; context per ADR-004 §6)."""

from __future__ import annotations

import json
import math
from importlib import resources
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

PLACEHOLDER = "__MAPQA_DATA__"


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _ids(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    return [str(v) for v in value] if isinstance(value, list) else str(value).split(";")


def render_viewer(data: dict[str, Any]) -> str:
    template = resources.files("mapqa.outputs").joinpath("viewer_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return template.replace(PLACEHOLDER, payload)


def photo_lookup(photos: pd.DataFrame | None, photo_ids: set[str], thumbs_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Metadata of referenced photos; `thumb` is a file:// URI of an offline copy (files source only)."""
    if photos is None or not photo_ids:
        return {}
    out = {}
    for row in photos[photos["photo_id"].isin(photo_ids)].to_dict("records"):
        thumb = thumbs_dir / str(row.get("filename")) if thumbs_dir else None
        captured = row.get("captured_at")
        out[str(row["photo_id"])] = {
            "captured_at": None if captured is None or pd.isna(captured) else pd.Timestamp(captured).isoformat(),
            "author": _clean(row.get("author")),
            "license": _clean(row.get("license")),
            "source": _clean(row.get("source")),
            "source_url": _clean(row.get("source_url")),
            "thumb": thumb.resolve().as_uri() if thumb is not None and thumb.is_file() else None,
        }
    return out


def road_context(roads: gpd.GeoDataFrame, osm_ids: set[str]) -> dict[str, Any]:
    """GeoJSON (EPSG:4326, 7 decimals) of the OSM ways sign candidates refer to (PRD 9.3)."""
    if not osm_ids:
        return {}
    subset = roads[roads["osm_id"].astype(str).isin(osm_ids)].to_crs(4326)
    rounded = shapely.transform(np.array(list(subset.geometry), dtype=object), lambda xy: np.round(xy, 7))
    out = {}
    for osm_id, geom in zip(subset["osm_id"], rounded):
        mapping = shapely.geometry.mapping(geom)
        out[str(osm_id)] = json.loads(json.dumps(mapping))  # tuples -> lists
    return dict(sorted(out.items()))


def build_viewer_context(records: list[dict[str, Any]], roads: gpd.GeoDataFrame, photos: pd.DataFrame | None,
                         thumbs_dir: Path | None) -> dict[str, Any]:
    referenced: set[str] = set()
    sign_ways: set[str] = set()
    for rec in records:
        referenced.update(_ids(rec.get("photo_ids")))
        if rec.get("type") == "sign_road":
            sign_ways.update(_ids(rec.get("osm_ids")))
    return {"roads": road_context(roads, sign_ways), "photos": photo_lookup(photos, referenced, thumbs_dir)}


def build_viewer_data(summary: dict[str, Any], records: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {key: summary[key] for key in ("source", "snapshot", "inputs", "counts", "config", "run_at")},
        "candidates": records,
        "photos": context.get("photos", {}),
        "roads": context.get("roads", {}),
    }


def write_viewer(path: Path, data: dict[str, Any]) -> None:
    path.write_text(render_viewer(data), encoding="utf-8", newline="\n")
