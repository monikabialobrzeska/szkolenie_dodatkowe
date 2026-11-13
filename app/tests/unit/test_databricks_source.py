"""ADR-004 §4-§5 — DatabricksSource settings, check() and fingerprints, replayed from real captures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mapqa.config import RunConfig
from mapqa.sources.base import SourceUnavailable
from mapqa.sources.databricks import DatabricksSource

CAPTURES = Path(__file__).resolve().parents[1] / "fixtures" / "databricks"
ENV = {"DATABRICKS_HOST": "https://example.cloud.databricks.com", "DATABRICKS_TOKEN": "dapi-sentinel-123",
       "DATABRICKS_SQL_WAREHOUSE_ID": "wh-env"}


def capture(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))


class ByStatement:
    """Answers each statement with the captured response whose SQL keyword matches."""

    def __init__(self, routes: dict[str, str]):
        self.routes = routes
        self.statements: list[str] = []

    def __call__(self, method, url, body, headers):
        if method == "POST":
            statement = json.loads(body)["statement"]
            self.statements.append(statement)
            name = next(n for key, n in self.routes.items() if key in statement)
            return 200, "application/json", json.dumps(capture(name)["exchanges"][-1]["body"])
        raise AssertionError(f"unexpected {method}")


def source(env=ENV, databricks=None, transport=None) -> DatabricksSource:
    config = RunConfig(source_kind="databricks", databricks=databricks or {})
    return DatabricksSource(config, env=env, transport=transport)


def test_settings_precedence_and_missing_values():
    s = source(databricks={"host": "https://toml.cloud.databricks.com", "warehouse_id": "wh-toml"})
    assert (s.host, s.warehouse_id, s.schema) == ("https://toml.cloud.databricks.com", "wh-toml", "workspace.map_qa")
    assert source().warehouse_id == "wh-env"  # env is the fallback
    with pytest.raises(SourceUnavailable, match="missing DATABRICKS_TOKEN") as info:
        source(env={k: v for k, v in ENV.items() if k != "DATABRICKS_TOKEN"}).check()
    assert "dapi" not in str(info.value)
    with pytest.raises(SourceUnavailable, match="schema"):
        source(databricks={"schema": "workspace.map_qa; DROP"}).check()


def test_location_names_schema_not_host():
    assert source().location() == "workspace.map_qa (warehouse wh-env)"


def test_check_reports_every_table_and_snapshot():
    transport = ByStatement({"information_schema": "information_schema_columns", "read_files": "volume_sources_json"})
    statuses = source(transport=transport).check()
    assert [s.name for s in statuses] == ["osm_roads", "overture_segments", "traffic_signs", "photos",
                                          "photo_road_links", "snapshot"]
    assert all(s.ok for s in statuses), [s.problem for s in statuses if not s.ok]
    assert all(st.lstrip().upper().startswith("SELECT") for st in transport.statements)


def test_fingerprints_are_table_versions_and_snapshot_hash():
    transport = ByStatement({"DESCRIBE HISTORY": "describe_history_osm_roads", "read_files": "volume_sources_json"})
    sums = source(transport=transport).checksums()
    assert sums["osm_roads"] == "delta-version:1"
    assert sums["snapshot"].startswith("sha256:") and len(sums["snapshot"]) == 7 + 64
    assert set(sums) == {"osm_roads", "overture_segments", "traffic_signs", "photos", "photo_road_links", "snapshot"}
