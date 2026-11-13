"""Local-files source adapter (ADR-001 §4.3-§4.6, ADR-004 §3). Read-only on the data folder."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import pyogrio

from mapqa.config import RunConfig
from mapqa.sources.base import InputBundle, InputError, InputStatus, SourceUnavailable
from mapqa.sources.normalise import links_frame, overture_frame, photos_frame, roads_frame, signs_frame

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
    "photo_road_links": ["photo_id", "osm_way_id", "overture_id", "distance_m", "candidate_flags"],
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


def _frame(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> pd.DataFrame:
    # Arrow conversion: avoids a pandas 3.1 deprecation triggered inside DuckDB's .df()
    return con.execute(sql, params).to_arrow_table().to_pandas()


class FilesSource:
    kind = "files"

    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self.data_dir = config.data_dir

    def location(self) -> str:
        return str(self.data_dir)

    def input_locations(self) -> dict[str, str]:
        return {name: str(self.data_dir / filename) for name, filename in FILES.items()}

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
            sums[name] = "sha256:" + digest.hexdigest()
        return sums

    def load(self) -> InputBundle:
        failed = [s for s in self.check() if not s.ok]
        if failed:
            raise InputError(failed[0].problem)
        read_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = self._connect()
        tags = ", ".join(f"json_extract_string(tags, '$.\"{tag}\"') AS {col}" for col, tag in TAG_KEYS.items())
        try:
            roads_raw = _frame(con, "SELECT osm_id, osm_version, osm_timestamp, highway, maxspeed, oneway, bridge, "
                                    f"tunnel, layer, name, {tags}, ST_AsWKB(geometry) AS geometry_wkb "
                                    "FROM read_parquet(?)", [str(self._path("osm_roads"))])
            roads = roads_frame(roads_raw)
        except (duckdb.Error, ValueError) as exc:
            raise InputError(f"osm_roads.parquet: cannot read roads ({exc})") from exc
        try:
            signs_raw = _frame(con, "SELECT id, object_value, first_seen_at, last_seen_at, image_ids, source_url, "
                                    "ST_AsWKB(geom) AS geometry_wkb FROM ST_Read(?)", [str(self._path("traffic_signs"))])
            signs, outside = signs_frame(signs_raw, self.config.bbox)
        except (duckdb.Error, ValueError) as exc:
            raise InputError(f"traffic_signs.geojson: cannot read signs ({exc})") from exc
        photos = photos_frame(_frame(con, "SELECT * FROM read_csv(?, types={'photo_id': 'VARCHAR', 'heading': 'DOUBLE'})",
                                     [str(self._path("photos"))]))
        links = links_frame(_frame(con, "SELECT * FROM read_csv(?, types={'photo_id': 'VARCHAR', 'osm_way_id': 'BIGINT', "
                                        "'distance_m': 'DOUBLE', 'overture_id': 'VARCHAR', 'candidate_flags': 'VARCHAR'})",
                                   [str(self._path("photo_road_links"))]))
        overture = overture_frame(_frame(con, "SELECT id, class FROM read_parquet(?)",
                                         [str(self._path("overture_segments"))]))
        snapshot = json.loads(self._path("snapshot").read_text(encoding="utf-8"))
        return InputBundle(
            roads=roads, signs=signs, photos=photos, photo_links=links, overture=overture,
            row_counts={"osm_roads": len(roads), "overture_segments": len(overture), "photos": len(photos),
                        "photo_road_links": len(links), "traffic_signs": len(signs) + outside},
            snapshot={"created_at": snapshot.get("created_at"),
                      "osm_extract_date": snapshot.get("osm", {}).get("extract_date"),
                      "overture_release": snapshot.get("overture", {}).get("release"),
                      "bbox_wgs84": snapshot.get("bbox_wgs84")},
            read_at=read_at,
            skipped={"outside_area": outside},
        )
