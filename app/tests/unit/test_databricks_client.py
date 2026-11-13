"""ADR-004 §8 — REST client, driven only by real captured responses (app/tests/fixtures/databricks/)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mapqa.sources.base import SourceUnavailable
from mapqa.sources.databricks_client import DatabricksClient

CAPTURES = Path(__file__).resolve().parents[1] / "fixtures" / "databricks"
TOKEN = "dapi-sentinel-0000000000000000000000"  # never a real token


def load(name: str) -> dict:
    return json.loads((CAPTURES / f"{name}.json").read_text(encoding="utf-8"))


class Replay:
    """Plays back captured exchanges in order and records what the client asked for."""

    def __init__(self, exchanges: list[dict], rehydrate: bool = False):
        self.exchanges = [copy.deepcopy(e) for e in exchanges]
        self.requests: list[tuple[str, str, dict | None, dict]] = []
        if rehydrate:  # measurement captures keep only the shape; restore row counts with empty rows
            for e in self.exchanges:
                body = e["body"]
                holder = body.get("result", body) if isinstance(body, dict) else {}
                if isinstance(holder, dict) and holder.get("data_array_truncated_in_fixture"):
                    width = len(self._schema()) if self._schema() else 1
                    holder["data_array"] = [[None] * width for _ in range(holder["data_array_rows_received"])]

    def _schema(self):
        for e in self.exchanges:
            body = e["body"]
            if isinstance(body, dict) and "manifest" in body:
                return body["manifest"]["schema"]["columns"]
        return None

    def __call__(self, method, url, body, headers):
        self.requests.append((method, url, json.loads(body) if body else None, headers))
        e = self.exchanges.pop(0)
        payload = e["body"] if isinstance(e["body"], str) else json.dumps(e["body"])
        return e["http_status"], e["headers"].get("content-type", "application/json"), payload


def client(transport) -> DatabricksClient:
    return DatabricksClient(host="https://example.cloud.databricks.com", token=TOKEN, warehouse_id="wh1",
                            transport=transport, poll_interval=0, sleep=lambda s: None)


def test_polls_same_statement_until_succeeded():
    capture = load("poll_sequence")
    replay = Replay(capture["exchanges"])
    result = client(replay).execute("SELECT count(*) AS photos FROM workspace.map_qa.photos", step="count")
    assert result.rows == [[561]]
    assert result.columns == [("photos", "LONG")]
    statement_id = capture["exchanges"][0]["body"]["statement_id"]
    assert [r[0] for r in replay.requests] == ["POST", "GET"]
    assert replay.requests[1][1].endswith(f"/api/2.0/sql/statements/{statement_id}")
    post_body = replay.requests[0][2]
    assert post_body["disposition"] == "INLINE" and post_body["format"] == "JSON_ARRAY"
    assert post_body["warehouse_id"] == "wh1" and post_body["on_wait_timeout"] == "CONTINUE"
    assert replay.requests[0][3]["Authorization"] == f"Bearer {TOKEN}"


def test_follows_every_chunk_link_in_order():
    capture = load("osm_projection_full_inline")
    replay = Replay(capture["exchanges"], rehydrate=True)
    result = client(replay).execute(capture["request"]["statement"], step="roads")
    assert len(result.rows) == 99768
    links = [e["body"].get("next_chunk_internal_link") or e["body"].get("result", {}).get("next_chunk_internal_link")
             for e in capture["exchanges"]]
    requested = [url for method, url, _, _ in replay.requests if method == "GET"]
    assert [u.split(".com", 1)[1] for u in requested] == [link for link in links if link]


def test_row_count_mismatch_is_an_error():
    capture = load("osm_projection_full_inline")
    replay = Replay(capture["exchanges"], rehydrate=False)  # rows missing on purpose
    with pytest.raises(SourceUnavailable, match="read 0 of 99768 rows"):
        client(replay).execute(capture["request"]["statement"], step="roads")


def test_type_conversion_from_schema():
    roads = client(Replay(load("osm_projection_sample")["exchanges"])).execute(
        load("osm_projection_sample")["request"]["statement"], step="roads")
    row = dict(zip([c for c, _ in roads.columns], roads.rows[0]))
    assert row["osm_id"] == 19846887 and isinstance(row["osm_version"], int)
    assert row["maxspeed"] == "50" and row["maxspeed_forward"] is None
    assert row["name"] == "Rusałek"
    signs = client(Replay(load("signs_sample")["exchanges"])).execute(load("signs_sample")["request"]["statement"], step="signs")
    srow = dict(zip([c for c, _ in signs.columns], signs.rows[0]))
    assert isinstance(srow["image_ids"], list) and srow["image_ids"][0].isdigit()
    assert srow["first_seen_at"] == "2024-07-05T16:58:57+0000"


def test_failed_statement_is_a_source_error_not_empty():
    capture = load("failed_missing_table")
    with pytest.raises(SourceUnavailable, match="statement FAILED.*TABLE_OR_VIEW_NOT_FOUND") as info:
        client(Replay(capture["exchanges"])).execute(capture["request"]["statement"], step="roads")
    assert TOKEN not in str(info.value)


@pytest.mark.parametrize(
    ("status", "content_type", "body", "message"),
    [
        (401, "application/json", '{"error_code":"UNAUTHENTICATED"}', "credentials rejected"),
        (403, "application/json", '{"error_code":"PERMISSION_DENIED"}', "credentials rejected"),
        (400, "text/plain", "Bad Request", "request rejected"),  # observed 2026-11-13 with a malformed token
    ],
)
def test_http_errors(status, content_type, body, message):
    def transport(method, url, data, headers):
        return status, content_type, body

    with pytest.raises(SourceUnavailable, match=message) as info:
        client(transport).execute("SELECT 1", step="check")
    assert TOKEN not in str(info.value)


def test_read_only_guard():
    def transport(*args):
        raise AssertionError("no request may be sent")

    for statement in ("DROP TABLE workspace.map_qa.photos", "  insert into x values (1)", "CREATE TABLE t AS SELECT 1",
                      "SELECT 1; DROP TABLE x"):
        with pytest.raises(ValueError, match="read-only"):
            client(transport).execute(statement, step="x")
