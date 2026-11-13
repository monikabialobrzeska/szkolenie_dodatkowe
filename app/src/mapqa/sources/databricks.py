"""Read-only Databricks source for the five workspace.map_qa tables (ADR-004)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Mapping

import pandas as pd

from mapqa.config import RunConfig
from mapqa.sources.base import InputBundle, InputError, InputStatus, SourceUnavailable
from mapqa.sources.databricks_client import DatabricksClient, Transport
from mapqa.sources.normalise import (links_frame, overture_frame, photos_frame, roads_frame, signs_frame)

TABLES = ["osm_roads", "overture_segments", "traffic_signs", "photos", "photo_road_links"]
ROADS_COLUMNS = ["osm_id", "osm_version", "osm_timestamp", "highway", "maxspeed", "oneway", "bridge", "tunnel",
                 "layer", "name"]
SIGNS_COLUMNS = ["id", "object_value", "first_seen_at", "last_seen_at", "image_ids", "source_url"]
PHOTOS_COLUMNS = ["photo_id", "filename", "source", "source_url", "author", "license", "captured_at", "longitude",
                  "latitude", "heading"]
LINKS_COLUMNS = ["photo_id", "osm_way_id", "overture_id", "distance_m", "candidate_flags"]
TAG_KEYS = {
    "maxspeed_forward": "maxspeed:forward",
    "maxspeed_backward": "maxspeed:backward",
    "maxspeed_conditional": "maxspeed:conditional",
    "maxspeed_variable": "maxspeed:variable",
    "source_maxspeed": "source:maxspeed",
    "maxspeed_type": "maxspeed:type",
}
REQUIRED = {
    "osm_roads": [*ROADS_COLUMNS, "tags", "geometry"],
    "overture_segments": ["id", "class", "geometry"],
    "traffic_signs": [*SIGNS_COLUMNS, "geometry"],
    "photos": PHOTOS_COLUMNS,
    "photo_road_links": LINKS_COLUMNS,
}
ROAD_PARTITIONS = 4  # ADR-004 §6: ~6.3 MB per partition, 4x headroom below the 25 MiB INLINE limit
_IDENT = re.compile(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+")


class DatabricksSource:
    kind = "databricks"

    def __init__(self, config: RunConfig, env: Mapping[str, str] | None = None,
                 transport: Transport | None = None) -> None:
        env = os.environ if env is None else env
        settings = config.databricks or {}
        self.config = config
        self.host = settings.get("host") or env.get("DATABRICKS_HOST")
        self.warehouse_id = settings.get("warehouse_id") or env.get("DATABRICKS_SQL_WAREHOUSE_ID")
        self.schema = settings.get("schema") or "workspace.map_qa"
        self._token = env.get("DATABRICKS_TOKEN")
        self._transport = transport
        self._client_instance: DatabricksClient | None = None

    # ---- settings -------------------------------------------------------------------------------------------
    def _client(self) -> DatabricksClient:
        if self._client_instance:
            return self._client_instance
        for value, name in ((self.host, "DATABRICKS_HOST (or source.databricks.host)"),
                            (self.warehouse_id, "DATABRICKS_SQL_WAREHOUSE_ID (or source.databricks.warehouse_id)"),
                            (self._token, "DATABRICKS_TOKEN")):
            if not value:
                raise SourceUnavailable(f"databricks: missing {name}")
        if not str(self.host).startswith("https://"):
            raise SourceUnavailable("databricks: host must start with https://")
        if not _IDENT.fullmatch(self.schema):
            raise SourceUnavailable("databricks: schema must look like catalog.schema")
        self._client_instance = DatabricksClient(self.host, self._token, self.warehouse_id, transport=self._transport)
        return self._client_instance

    def location(self) -> str:
        return f"{self.schema} (warehouse {self.warehouse_id})"

    def input_locations(self) -> dict[str, str]:
        catalog, schema = self.schema.split(".")
        out = {name: f"{self.schema}.{name}" for name in TABLES}
        out["snapshot"] = f"/Volumes/{catalog}/{schema}/inputs/sources.json"
        return out

    def _query(self, statement: str, step: str) -> list[dict]:
        return self._client().execute(statement, step=step).records()

    def _snapshot_text(self) -> str:
        path = self.input_locations()["snapshot"]
        rows = self._query(f"SELECT value FROM read_files('{path}', format => 'text', wholeText => true)", "snapshot")
        if not rows:
            raise InputError(f"databricks: {path} is empty or missing")
        return rows[0]["value"]

    # ---- adapter interface --------------------------------------------------------------------------------------
    def check(self) -> list[InputStatus]:
        catalog, schema = self.schema.split(".")
        rows = self._query(f"SELECT table_name, column_name FROM {catalog}.information_schema.columns "
                           f"WHERE table_schema = '{schema}'", "check tables")
        columns: dict[str, set[str]] = {}
        for row in rows:
            columns.setdefault(row["table_name"], set()).add(row["column_name"])
        statuses = []
        for table in TABLES:
            location = f"{self.schema}.{table}"
            if table not in columns:
                statuses.append(InputStatus(table, location, False, f"{location}: table not found"))
                continue
            missing = [c for c in REQUIRED[table] if c not in columns[table]]
            statuses.append(InputStatus(table, location, not missing,
                                        f"{location}: missing columns {', '.join(missing)}" if missing else None))
        try:
            json.loads(self._snapshot_text())
            statuses.append(InputStatus("snapshot", self.input_locations()["snapshot"], True))
        except (InputError, SourceUnavailable, ValueError) as exc:
            statuses.append(InputStatus("snapshot", self.input_locations()["snapshot"], False, str(exc)))
        return statuses

    def checksums(self) -> dict[str, str]:
        sums = {}
        for table in TABLES:
            rows = self._query(f"DESCRIBE HISTORY {self.schema}.{table} LIMIT 1", f"version {table}")
            sums[table] = f"delta-version:{rows[0]['version']}" if rows else "delta-version:unknown"
        sums["snapshot"] = "sha256:" + hashlib.sha256(self._snapshot_text().encode("utf-8")).hexdigest()
        return sums

    def load(self) -> InputBundle:
        read_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tags = ", ".join(f"get_json_object(tags, \"$['{key}']\") AS {col}" for col, key in TAG_KEYS.items())
        road_rows: list[dict] = []
        for k in range(ROAD_PARTITIONS):
            road_rows += self._query(
                f"SELECT {', '.join(ROADS_COLUMNS)}, {tags}, base64(geometry) AS geometry_b64 "
                f"FROM {self.schema}.osm_roads WHERE pmod(osm_id, {ROAD_PARTITIONS}) = {k}",
                f"read osm_roads part {k + 1}/{ROAD_PARTITIONS}")
        roads = roads_frame(_decode(road_rows))
        sign_rows = self._query(f"SELECT {', '.join(SIGNS_COLUMNS)}, base64(geometry) AS geometry_b64 "
                                f"FROM {self.schema}.traffic_signs", "read traffic_signs")
        signs, outside = signs_frame(_decode(sign_rows), self.config.bbox)
        # JSON_ARRAY truncates TIMESTAMP to milliseconds; format on the server to keep the stored microseconds
        # (capture photos_timestamp_precision.json)
        photo_select = ", ".join(
            "date_format(captured_at, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX\") AS captured_at" if c == "captured_at" else c
            for c in PHOTOS_COLUMNS)
        photos = photos_frame(pd.DataFrame(self._query(f"SELECT {photo_select} FROM {self.schema}.photos",
                                                       "read photos"), columns=PHOTOS_COLUMNS))
        links = links_frame(pd.DataFrame(self._query(
            f"SELECT {', '.join(LINKS_COLUMNS)} FROM {self.schema}.photo_road_links", "read photo_road_links"),
            columns=LINKS_COLUMNS))
        overture = overture_frame(pd.DataFrame(self._query(f"SELECT id, class FROM {self.schema}.overture_segments",
                                                           "read overture_segments"), columns=["id", "class"]))
        snapshot = json.loads(self._snapshot_text())
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


def _decode(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["geometry_wkb"] = [base64.b64decode(v) for v in frame.pop("geometry_b64")]
    return frame
