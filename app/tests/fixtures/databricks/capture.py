"""Capture real Databricks SQL Statement Execution API responses for test fixtures (AGENTS.md mock rule).

Read-only: only fixed SELECT / DESCRIBE statements. Credentials come from DATABRICKS_HOST, DATABRICKS_TOKEN and
DATABRICKS_SQL_WAREHOUSE_ID and are never written: the Authorization header is not recorded, response cookies are
dropped, and every saved file is checked for the token value before the script exits.

Run from app/:  uv run python tests/fixtures/databricks/capture.py <case> [<case> ...]   (or "all")
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
KEEP_HEADERS = ("content-type", "content-length", "x-databricks-org-id", "x-request-id")
OSM_PROJECTION = (
    "SELECT osm_id, osm_version, osm_timestamp, highway, maxspeed, oneway, bridge, tunnel, layer, name, "
    "get_json_object(tags, \"$['maxspeed:forward']\") AS maxspeed_forward, "
    "get_json_object(tags, \"$['maxspeed:backward']\") AS maxspeed_backward, "
    "get_json_object(tags, \"$['maxspeed:conditional']\") AS maxspeed_conditional, "
    "get_json_object(tags, \"$['maxspeed:variable']\") AS maxspeed_variable, "
    "get_json_object(tags, \"$['source:maxspeed']\") AS source_maxspeed, "
    "get_json_object(tags, \"$['maxspeed:type']\") AS maxspeed_type, "
    "base64(geometry) AS geometry_b64 FROM workspace.map_qa.osm_roads"
)
CASES = {
    "describe_osm_roads": dict(statement="DESCRIBE TABLE workspace.map_qa.osm_roads"),
    "describe_overture_segments": dict(statement="DESCRIBE TABLE workspace.map_qa.overture_segments"),
    "describe_photos": dict(statement="DESCRIBE TABLE workspace.map_qa.photos"),
    "describe_photo_road_links": dict(statement="DESCRIBE TABLE workspace.map_qa.photo_road_links"),
    "describe_traffic_signs": dict(statement="DESCRIBE TABLE workspace.map_qa.traffic_signs"),
    "osm_projection_sample": dict(statement=OSM_PROJECTION + " WHERE osm_id IN (907138970, 224814473, 19846887) ORDER BY osm_id"),
    "signs_sample": dict(statement="SELECT id, object_value, first_seen_at, last_seen_at, image_ids, source_url, "
                                   "base64(geometry) AS geometry_b64 FROM workspace.map_qa.traffic_signs ORDER BY id LIMIT 3"),
    "failed_missing_table": dict(statement="SELECT count(*) FROM workspace.map_qa.no_such_table"),
    "poll_sequence": dict(statement="SELECT count(*) AS photos FROM workspace.map_qa.photos", wait_timeout="0s"),
    # measurement: full projected roads table, INLINE; data_array of every chunk is NOT stored (size), only its shape
    "osm_projection_full_inline": dict(statement=OSM_PROJECTION + " ORDER BY osm_id", keep_rows=0),
    "osm_projection_partition_0_of_4": dict(statement=OSM_PROJECTION + " WHERE pmod(osm_id, 4) = 0 ORDER BY osm_id", keep_rows=0),
    "information_schema_columns": dict(statement="SELECT table_name, column_name, data_type FROM workspace.information_schema.columns "
                                                 "WHERE table_schema = 'map_qa' ORDER BY table_name, ordinal_position"),
    "describe_history_osm_roads": dict(statement="DESCRIBE HISTORY workspace.map_qa.osm_roads LIMIT 1"),
    "photos_timestamp_precision": dict(statement=(
        "SELECT photo_id, captured_at, date_format(captured_at, \"yyyy-MM-dd'T'HH:mm:ss.SSSSSSXXX\") AS captured_at_us "
        "FROM workspace.map_qa.photos WHERE photo_id IN ('mapillary_1003606477244774', "
        "'panoramax_334a574d-18cb-4c2e-be3f-f99307e080d9') ORDER BY photo_id")),
    "volume_sources_json": dict(statement="SELECT value FROM read_files('/Volumes/workspace/map_qa/inputs/sources.json', "
                                          "format => 'text', wholeText => true)"),
}


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"missing environment variable {name}")
    return value


HOST, TOKEN, WAREHOUSE = env("DATABRICKS_HOST").rstrip("/"), env("DATABRICKS_TOKEN"), env("DATABRICKS_SQL_WAREHOUSE_ID")


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(HOST + path, data=data, method=method,
                  headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urlopen(req, timeout=60) as resp:
            status, headers, raw = resp.status, resp.headers, resp.read()
    except HTTPError as exc:
        status, headers, raw = exc.code, exc.headers, exc.read()
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return {
        "method": method, "path": path, "http_status": status,
        "headers": {k: headers.get(k) for k in KEEP_HEADERS if headers.get(k) is not None},
        "elapsed_s": round(time.monotonic() - started, 2), "body_bytes": len(raw),
        "body": parsed if parsed is not None else text,
    }


def shrink(exchange: dict, keep_rows: int | None) -> dict:
    """For measurement cases keep the structure but not all rows. Works on a copy: the caller still counts rows."""
    if keep_rows is None:
        return exchange
    exchange = copy.deepcopy(exchange)
    body = exchange.get("body")
    holder = body.get("result") if isinstance(body, dict) and "result" in body else body
    if isinstance(holder, dict) and isinstance(holder.get("data_array"), list):
        rows = holder["data_array"]
        holder["data_array_rows_received"] = len(rows)
        holder["data_array"] = rows[:keep_rows]
        holder["data_array_truncated_in_fixture"] = True
    return exchange


def capture(name: str, statement: str, wait_timeout: str = "10s", keep_rows: int | None = None) -> dict:
    request = {"warehouse_id": WAREHOUSE, "statement": statement, "wait_timeout": wait_timeout,
               "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY"}
    record = {"case": name, "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "target": "SQL Statement Execution API 2.0, schema workspace.map_qa", "request": request, "exchanges": []}
    first = call("POST", "/api/2.0/sql/statements", request)
    record["exchanges"].append(shrink(first, keep_rows))
    body = first["body"] if isinstance(first["body"], dict) else {}
    statement_id = body.get("statement_id")
    deadline = time.monotonic() + 300
    while statement_id and body.get("status", {}).get("state") in ("PENDING", "RUNNING") and time.monotonic() < deadline:
        time.sleep(2)
        nxt = call("GET", f"/api/2.0/sql/statements/{statement_id}")
        record["exchanges"].append(shrink(nxt, keep_rows))
        body = nxt["body"] if isinstance(nxt["body"], dict) else {}
    chunk = body.get("result") or {}
    total_rows = len(chunk.get("data_array") or [])
    while chunk.get("next_chunk_internal_link"):
        nxt = call("GET", chunk["next_chunk_internal_link"])
        chunk = nxt["body"] if isinstance(nxt["body"], dict) else {}
        total_rows += len(chunk.get("data_array") or [])
        record["exchanges"].append(shrink(nxt, keep_rows if keep_rows is not None else 2))
    record["rows_read_total"] = total_rows
    return record


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PERSONAL_COLUMNS = {"userId", "userName"}  # DESCRIBE HISTORY identifies the person who changed the table


def redact(record: dict) -> dict:
    """Remove personal data (privacy) while keeping the response shape; recorded in `redactions`."""
    redactions = []
    for exchange in record["exchanges"]:
        body = exchange.get("body")
        if isinstance(body, dict) and "manifest" in body and "result" in body:
            names = [c["name"] for c in body["manifest"]["schema"]["columns"]]
            idx = [i for i, n in enumerate(names) if n in PERSONAL_COLUMNS]
            for row in body["result"].get("data_array") or []:
                for i in idx:
                    if row[i] is not None:
                        row[i] = "<redacted>"
            if idx:
                redactions.append(f"columns {sorted(PERSONAL_COLUMNS & set(names))} replaced by <redacted>")
    text = json.dumps(record, ensure_ascii=False)
    if EMAIL.search(text):
        redactions.append("e-mail addresses replaced by <email redacted>")
        record = json.loads(EMAIL.sub("<email redacted>", text))
    record["redactions"] = redactions
    return record


def main() -> None:
    names = list(CASES) if sys.argv[1:] == ["all"] else sys.argv[1:]
    written = []
    for name in names:
        record = redact(capture(name, **CASES[name]))
        path = HERE / f"{name}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        written.append(path)
        states = [e["body"].get("status", {}).get("state") if isinstance(e["body"], dict) else e["http_status"]
                  for e in record["exchanges"]]
        print(f"{name}: exchanges={len(record['exchanges'])} states={states} rows={record['rows_read_total']} "
              f"bytes={[e['body_bytes'] for e in record['exchanges']][:6]}")
    leaked = [p.name for p in written if TOKEN in p.read_text(encoding="utf-8")]
    if leaked:
        for p in written:
            p.unlink()
        raise SystemExit(f"token found in {leaked}; all captures deleted")
    print("token sentinel check: clean")


if __name__ == "__main__":
    main()
