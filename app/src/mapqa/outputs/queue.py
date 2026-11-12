"""review_queue.csv and review_queue.geojson (ADR-000 §6.2). Deterministic output."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import numpy as np
import shapely

from mapqa.models import Candidate

COLUMNS = [
    "rank", "candidate_id", "type", "state", "reason_code", "explanation", "road_class", "osm_ids", "sign_id",
    "osm_value_raw", "osm_value_kmh", "osm_forward_kmh", "osm_backward_kmh", "sign_value_kmh", "diff_kmh",
    "distance_m", "sign_first_seen_at", "sign_last_seen_at", "photo_count", "photo_ids", "osm_version",
    "osm_timestamp", "lon", "lat",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def to_records(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """One plain dict per candidate with EPSG:4326 geometry (GeoJSON mapping) and representative lon/lat."""
    if not candidates:
        return []
    geoms = gpd.GeoSeries([c.geometry for c in candidates], crs=2180).to_crs(4326)
    reps = [g if g.geom_type == "Point" else g.interpolate(0.5, normalized=True) for g in geoms]
    rounded = shapely.transform(np.array(list(geoms), dtype=object), lambda xy: np.round(xy, 7))
    records = []
    for c, geom, rep in zip(candidates, rounded, reps):
        rec = {col: getattr(c, col) for col in COLUMNS if col not in ("lon", "lat")}
        rec["lon"] = f"{rep.x:.6f}"
        rec["lat"] = f"{rep.y:.6f}"
        rec["geometry"] = shapely.geometry.mapping(geom)
        records.append(rec)
    return records


def write_csv(path: Path, records: Iterable[dict[str, Any]]) -> None:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for rec in records:
        writer.writerow([_text(rec[col]) for col in COLUMNS])
    path.write_text(buffer.getvalue(), encoding="utf-8", newline="")


def _json_ready(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_ready(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 7)
    return value


def write_geojson(path: Path, records: Iterable[dict[str, Any]]) -> None:
    features = []
    for rec in records:
        props = {col: rec[col] for col in COLUMNS if col not in ("lon", "lat")}
        features.append({"type": "Feature", "properties": props, "geometry": _json_ready(rec["geometry"])})
    collection = {"type": "FeatureCollection", "features": features}
    text = json.dumps(collection, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8", newline="")
