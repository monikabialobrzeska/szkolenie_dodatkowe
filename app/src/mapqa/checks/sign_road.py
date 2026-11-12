"""Check 2 — sign vs road (ADR-002 §4-§6.2, PRD AC-12-AC-14, AC-17b)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
import shapely

from mapqa.models import Candidate, SpeedLimit, iso

_SIGN = re.compile(r"regulatory--maximum-speed-limit-(\d+)--g\d+")
BRIDGE_VALUES = {"yes", "viaduct"}
TUNNEL_VALUES = {"yes", "covered"}


@dataclass(frozen=True)
class SignInfo:
    sign_id: str
    value_kmh: int | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    geometry: Any


@dataclass(frozen=True)
class RoadInfo:
    osm_id: int
    highway: str
    bridge: str | None
    tunnel: str | None
    osm_version: int
    osm_timestamp: datetime | None
    speed: SpeedLimit


def parse_sign_value(object_value: str | None) -> int | None:
    match = _SIGN.fullmatch(object_value or "")
    if not match:
        return None
    value = int(match.group(1))
    return value if 5 <= value <= 140 else None


def _require_2180(frame: gpd.GeoDataFrame, name: str) -> None:
    if frame.crs is None or frame.crs.to_epsg() != 2180:
        raise ValueError(f"{name} must be in EPSG:2180 before measuring distances, got {frame.crs}")


def associate(signs: gpd.GeoDataFrame, roads: gpd.GeoDataFrame, distance_m: float,
              excluded_classes: Iterable[str]) -> dict[str, list[tuple[int, float]]]:
    """Eligible roads within distance (inclusive) per sign, ordered by distance then osm_id."""
    _require_2180(signs, "signs")
    _require_2180(roads, "roads")
    eligible = roads[~roads["highway"].isin(set(excluded_classes))]
    sign_idx, road_idx = eligible.sindex.query(signs.geometry, predicate="dwithin", distance=distance_m)
    sign_geoms = signs.geometry.values[sign_idx]
    road_geoms = eligible.geometry.values[road_idx]
    distances = shapely.distance(sign_geoms, road_geoms)
    pairs = pd.DataFrame({
        "sign_id": signs["sign_id"].values[sign_idx],
        "osm_id": eligible["osm_id"].values[road_idx],
        "distance": distances,
    })
    pairs = pairs[pairs["distance"] <= distance_m].sort_values(["sign_id", "distance", "osm_id"])
    out: dict[str, list[tuple[int, float]]] = {}
    for row in pairs.itertuples(index=False):
        out.setdefault(str(row.sign_id), []).append((int(row.osm_id), float(row.distance)))
    return out


def _on_other_level(road: RoadInfo) -> bool:
    return (road.bridge in BRIDGE_VALUES) or (road.tunnel in TUNNEL_VALUES)


def _fmt(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def evaluate_sign(sign: SignInfo, matches: list[tuple[RoadInfo, float]], distance_limit_m: float) -> Candidate | None:
    """Decide the candidate for one sign; `matches` are eligible roads ordered by distance."""
    if not matches:
        return None
    nearest, distance = matches[0]
    speed = nearest.speed
    d = f"{distance_limit_m:g}"
    common = dict(
        type="sign_road",
        state="needs_review",
        sign_id=sign.sign_id,
        road_class=nearest.highway,
        geometry=sign.geometry,
        osm_value_raw=speed.raw_text(),
        osm_value_kmh=speed.value_kmh,
        osm_forward_kmh=speed.forward_kmh,
        osm_backward_kmh=speed.backward_kmh,
        sign_value_kmh=sign.value_kmh,
        distance_m=round(distance, 1),
        sign_first_seen_at=iso(sign.first_seen_at),
        sign_last_seen_at=iso(sign.last_seen_at),
        osm_version=nearest.osm_version,
        osm_timestamp=iso(nearest.osm_timestamp),
    )

    if len(matches) > 1:
        return Candidate(
            reason_code="multiple_candidate_roads",
            explanation=f"{len(matches)} roads lie within {d} m of the sign; it is unclear which one it governs.",
            osm_ids=[r.osm_id for r, _ in matches],
            **common,
        )

    value = sign.value_kmh
    if speed.kind == "numeric":
        known = [speed.value_kmh]
    elif speed.kind == "directional":
        known = [v for v in (speed.forward_kmh, speed.backward_kmh) if v is not None]
    else:
        known = []
    if value in known:
        return None  # AC-14
    diff = min(abs(value - k) for k in known) if known else None

    stale = (sign.last_seen_at is not None and nearest.osm_timestamp is not None
             and sign.last_seen_at < nearest.osm_timestamp)
    if _on_other_level(nearest):
        reason, text = "bridge_or_tunnel", (f"Sign shows {value} km/h; the only road within {d} m is a bridge or tunnel "
                                            "and may be on another level.")
    elif speed.variable:
        reason, text = "variable_limit", "Road has a variable speed limit (maxspeed:variable)."
    elif speed.conditional:
        reason, text = "conditional_limit", "Road has a conditional speed limit (maxspeed:conditional)."
    elif stale:
        reason, text = "stale_sign", (f"Sign shows {value} km/h but was last seen on {iso(sign.last_seen_at)[:10]}, "
                                      f"before the road was last edited in OSM on {iso(nearest.osm_timestamp)[:10]}.")
    elif speed.kind == "unparseable":
        reason, text = "unparseable_value", f"OSM speed-limit value '{speed.raw_text()}' cannot be interpreted."
    elif speed.kind == "missing":
        reason, text = "road_limit_missing", (f"Sign shows {value} km/h; the only road within {d} m has no "
                                              "speed-limit value in OSM.")
    elif speed.kind == "directional":
        reason, text = "direction_unknown", (f"Sign shows {value} km/h; the road has direction-specific limits "
                                             f"{_fmt(speed.forward_kmh)}/{_fmt(speed.backward_kmh)} km/h and the "
                                             "sign's direction is unknown.")
    else:
        reason, text = "sign_road_mismatch", (f"Sign shows {value} km/h; the only road within {d} m has "
                                              f"{speed.value_kmh} km/h in OSM.")
    return Candidate(reason_code=reason, explanation=text, osm_ids=[nearest.osm_id], diff_kmh=diff, **common)


def evaluate_signs(signs: gpd.GeoDataFrame, roads: gpd.GeoDataFrame, limits: dict[int, SpeedLimit],
                   distance_m: float, excluded_classes: Iterable[str]) -> tuple[list[Candidate], dict[str, int]]:
    """Run Check 2 for all signs. Returns candidates and skip/unmatched counts."""
    counts = {"sign_value_unparseable": 0, "unmatched_no_road_within_distance": 0, "sign_matches_road": 0}
    values = signs["object_value"].map(parse_sign_value)
    unparseable = values.isna()
    counts["sign_value_unparseable"] = int(unparseable.sum())
    valid = signs[~unparseable]
    matches = associate(valid, roads, distance_m, excluded_classes)
    road_rows = roads.set_index("osm_id")
    candidates: list[Candidate] = []
    for row in valid.itertuples(index=False):
        sign_matches = matches.get(str(row.sign_id), [])
        if not sign_matches:
            counts["unmatched_no_road_within_distance"] += 1
            continue
        infos = []
        for osm_id, dist in sign_matches:
            r = road_rows.loc[osm_id]
            infos.append((RoadInfo(osm_id=osm_id, highway=r["highway"], bridge=_none(r["bridge"]),
                                   tunnel=_none(r["tunnel"]), osm_version=int(r["osm_version"]),
                                   osm_timestamp=r["osm_timestamp"], speed=limits[osm_id]), dist))
        info = SignInfo(sign_id=str(row.sign_id), value_kmh=parse_sign_value(row.object_value),
                        first_seen_at=row.first_seen_at, last_seen_at=row.last_seen_at, geometry=row.geometry)
        candidate = evaluate_sign(info, infos, distance_m)
        if candidate is None:
            counts["sign_matches_road"] += 1
        else:
            candidates.append(candidate)
    return candidates, counts


def _none(value: Any) -> Any:
    return None if value is None or (isinstance(value, float) and pd.isna(value)) else value
