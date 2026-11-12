"""Local-files source adapter (ADR-001 §4.3-§4.6). Read-only on the data folder."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import pyogrio
import shapely

from mapqa.config import RunConfig
from mapqa.sources.base import InputBundle, InputError, InputStatus, SourceUnavailable

FILES = {
    "osm_roads": "osm_roads.parquet",
    "overture_segments": "overture_segments.parquet",
    "traffic_signs": "traffic_signs.geojson",
    "photos": "photos.csv",
    "photo_road_links": "photo_road_links.csv",
    "snapshot": "sources.json",
}
REQUIRED = {
    "osm_roads": ["osm_id", "osm_version", "osm_timestamp", "highway", "maxspeed", "oneway", "bridge", "tunnel",
                  "layer", "name", "tags", "geometry"],
    "overture_segments": ["id", "class", "geometry"],
    "traffic_signs": ["id", "object_value", "first_seen_at", "last_seen_at", "image_ids", "source_url"],
    "photos": ["photo_id", "filename", "longitude", "latitude", "heading", "captured_at", "author", "license",
               "source", "source_url"],
    "photo_road_links": ["photo_id", "osm_way_id", "distance_m", "candidate_flags"],
    "snapshot": ["created_at", "bbox_wgs84", "osm", "overture"],
}
TAG_KEYS = {
    "maxspeed_forward": "maxspeed:forward",
    "maxspeed_backward": "maxspeed:backward",
    "maxspeed_conditional": "maxspeed:conditional",
    "maxspeed_variable": "maxspeed:variable",
    "source_maxspeed": "source:maxspeed",
    "maxspeed_type": "maxspeed:type",
}


class FilesSource:
    kind = "files"

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.data_dir = config.data_dir

    def location(self) -> str:
        return str(self.data_dir)

    def _path(self, name: str) -> Path:
        return self.data_dir / FILES[name]

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()  # in-memory: nothing is written next to the inputs
        try:
            con.execute("LOAD spatial")
        except duckdb.Error as exc:
            raise SourceUnavailable(
                "files: DuckDB spatial extension is not installed; run once with internet access: "
                "uv run python -c \"import duckdb; duckdb.connect().execute('INSTALL spatial')\""
            ) from exc
        return con

    def _columns(self, con: duckdb.DuckDBPyConnection, name: str) -> list[str]:
        path = str(self._path(name))
        if name in ("osm_roads", "overture_segments"):
            return [r[0] for r in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [path]).fetchall()]
        if name in ("photos", "photo_road_links"):
            return [r[0] for r in con.execute("DESCRIBE SELECT * FROM read_csv(?)", [path]).fetchall()]
        if name == "traffic_signs":
            return list(pyogrio.read_info(path)["fields"])
        return list(json.loads(Path(path).read_text(encoding="utf-8")))

    def check(self) -> list[InputStatus]:
        if not self.data_dir.is_dir():
            raise InputError(f"data folder not found: {self.data_dir}")
        con = self._connect()
        statuses = []
        for name, filename in FILES.items():
            path = self._path(name)
            if not path.is_file():
                statuses.append(InputStatus(name, str(path), False, f"{filename}: file not found at {path}"))
                continue
            try:
                missing = [c for c in REQUIRED[name] if c not in self._columns(con, name)]
            except Exception as exc:  # unreadable or corrupt file
                statuses.append(InputStatus(name, str(path), False, f"{filename}: unreadable ({type(exc).__name__})"))
                continue
            if missing:
                statuses.append(InputStatus(name, str(path), False, f"{filename}: missing columns {', '.join(missing)}"))
            else:
                statuses.append(InputStatus(name, str(path), True))
        return statuses

    def checksums(self) -> dict[str, str]:
        sums = {}
        for name in FILES:
            digest = hashlib.sha256()
            with self._path(name).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            sums[name] = digest.hexdigest()
        return sums

    def load(self) -> InputBundle:
        failed = [s for s in self.check() if not s.ok]
        if failed:
            raise InputError(failed[0].problem)
        read_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = self._connect()
        roads = self._load_roads(con)
        overture = con.execute("SELECT id, class FROM read_parquet(?) ORDER BY id",
                               [str(self._path("overture_segments"))]).df()
        signs, outside = self._load_signs()
        photos = con.execute(
            "SELECT * FROM read_csv(?, types={'photo_id': 'VARCHAR', 'heading': 'DOUBLE', 'sequence': 'VARCHAR'}) "
            "ORDER BY photo_id", [str(self._path("photos"))]).df()
        links = con.execute(
            "SELECT * FROM read_csv(?, types={'photo_id': 'VARCHAR', 'osm_way_id': 'BIGINT', 'distance_m': 'DOUBLE', "
            "'overture_id': 'VARCHAR', 'candidate_flags': 'VARCHAR'}) ORDER BY photo_id, osm_way_id",
            [str(self._path("photo_road_links"))]).df()
        snapshot = json.loads(self._path("snapshot").read_text(encoding="utf-8"))
        return InputBundle(
            roads=roads,
            signs=signs,
            photos=photos,
            photo_links=links,
            overture=overture,
            row_counts={
                "osm_roads": len(roads),
                "overture_segments": len(overture),
                "photos": len(photos),
                "photo_road_links": len(links),
                "traffic_signs": len(signs) + outside,
            },
            snapshot={
                "created_at": snapshot.get("created_at"),
                "osm_extract_date": snapshot.get("osm", {}).get("extract_date"),
                "overture_release": snapshot.get("overture", {}).get("release"),
                "bbox_wgs84": snapshot.get("bbox_wgs84"),
            },
            read_at=read_at,
            skipped={"outside_area": outside},
        )

    def _load_roads(self, con: duckdb.DuckDBPyConnection) -> gpd.GeoDataFrame:
        extracts = ", ".join(f"json_extract_string(tags, '$.\"{tag}\"') AS {col}" for col, tag in TAG_KEYS.items())
        try:
            df = con.execute(
                "SELECT osm_id, osm_version, osm_timestamp, highway, maxspeed, oneway, bridge, tunnel, layer, name, "
                f"tags, {extracts}, ST_AsWKB(geometry) AS wkb FROM read_parquet(?) ORDER BY osm_id",
                [str(self._path("osm_roads"))]).df()
            timestamps = pd.to_datetime(df["osm_timestamp"], utc=True)
        except (duckdb.Error, ValueError) as exc:
            raise InputError(f"osm_roads.parquet: cannot read roads ({exc})") from exc
        geometry = shapely.from_wkb(df.pop("wkb").map(bytes).to_numpy())
        df["osm_timestamp"] = timestamps
        roads = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")  # CRS from GeoParquet metadata
        return roads.to_crs(2180)

    def _load_signs(self) -> tuple[gpd.GeoDataFrame, int]:
        signs = gpd.read_file(self._path("traffic_signs"))
        signs = signs.rename(columns={"id": "sign_id"})
        signs["sign_id"] = signs["sign_id"].astype(str)
        try:
            for column in ("first_seen_at", "last_seen_at"):
                signs[column] = pd.to_datetime(signs[column], utc=True)
        except ValueError as exc:
            raise InputError(f"traffic_signs.geojson: unparseable timestamp ({exc})") from exc
        min_lon, min_lat, max_lon, max_lat = self.config.bbox
        inside = signs.geometry.x.between(min_lon, max_lon) & signs.geometry.y.between(min_lat, max_lat)
        outside = int((~inside).sum())
        signs = signs[inside].set_crs("EPSG:4326", allow_override=True).to_crs(2180)
        return signs.sort_values("sign_id").reset_index(drop=True), outside
