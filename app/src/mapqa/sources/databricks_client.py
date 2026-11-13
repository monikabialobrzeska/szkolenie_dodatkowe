"""Minimal read-only client for the Databricks SQL Statement Execution API 2.0 (ADR-004 §3, §5).

Standard library only. The token is sent in the Authorization header and never appears in messages.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mapqa.sources.base import SourceUnavailable

Transport = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, str, str]]
_READ_ONLY = re.compile(r"\s*(SELECT|DESCRIBE)\b", re.IGNORECASE)
_INT_TYPES = {"LONG", "INT", "SHORT", "BYTE"}
_FLOAT_TYPES = {"DOUBLE", "FLOAT", "DECIMAL"}
_TEXT_TYPES = {"STRING", "TIMESTAMP", "DATE", "CHAR", "BINARY"}
_JSON_TYPES = {"ARRAY", "STRUCT", "MAP"}


@dataclass
class Result:
    columns: list[tuple[str, str]]  # (name, type_name)
    rows: list[list[Any]]

    def records(self) -> list[dict[str, Any]]:
        names = [name for name, _ in self.columns]
        return [dict(zip(names, row)) for row in self.rows]


def _urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, str, str]:
    request = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, response.headers.get("content-type", ""), response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.headers.get("content-type", "") if exc.headers else "", exc.read().decode("utf-8", "replace")


def _convert(value: Any, type_name: str, column: str) -> Any:
    if value is None:
        return None
    if type_name in _INT_TYPES:
        return int(value)
    if type_name in _FLOAT_TYPES:
        return float(value)
    if type_name == "BOOLEAN":
        return str(value).lower() == "true"
    if type_name in _TEXT_TYPES:
        return str(value)
    if type_name in _JSON_TYPES:
        return json.loads(value) if isinstance(value, str) else value
    raise SourceUnavailable(f"databricks: unsupported column type {type_name} for {column}")


class DatabricksClient:
    def __init__(self, host: str, token: str, warehouse_id: str, *, transport: Transport | None = None,
                 poll_interval: float = 2.0, timeout_s: float = 300.0, wait_timeout: str = "30s",
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.host = host.rstrip("/")
        self._token = token
        self.warehouse_id = warehouse_id
        self._transport = transport or _urllib_transport
        self.poll_interval, self.timeout_s, self.wait_timeout = poll_interval, timeout_s, wait_timeout
        self._sleep, self._clock = sleep, clock

    def _call(self, method: str, path: str, step: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        try:
            status, content_type, text = self._transport(method, self.host + path, body, headers)
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceUnavailable(f"databricks: {step}: unreachable ({type(exc).__name__})") from None
        if status in (401, 403):
            raise SourceUnavailable(f"databricks: {step}: credentials rejected (HTTP {status})")
        if status == 400 and "json" not in (content_type or ""):
            raise SourceUnavailable(f"databricks: {step}: request rejected (HTTP 400) — check the token format and host")
        if status >= 400:
            raise SourceUnavailable(f"databricks: {step}: HTTP {status}")
        try:
            return json.loads(text)
        except ValueError:
            raise SourceUnavailable(f"databricks: {step}: response is not JSON") from None

    def execute(self, statement: str, step: str) -> Result:
        if not _READ_ONLY.match(statement) or ";" in statement.strip().rstrip(";"):
            raise ValueError("databricks: only single read-only SELECT/DESCRIBE statements are allowed")
        body = self._call("POST", "/api/2.0/sql/statements", step, {
            "warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": self.wait_timeout,
            "on_wait_timeout": "CONTINUE", "disposition": "INLINE", "format": "JSON_ARRAY",
        })
        statement_id = body.get("statement_id")
        deadline = self._clock() + self.timeout_s
        while body.get("status", {}).get("state") in ("PENDING", "RUNNING"):
            if self._clock() > deadline:
                raise SourceUnavailable(f"databricks: {step}: no result after {self.timeout_s:g} s "
                                        f"(statement {statement_id} may still run; not re-submitted)")
            self._sleep(self.poll_interval)
            body = self._call("GET", f"/api/2.0/sql/statements/{statement_id}", step)
        state = body.get("status", {}).get("state")
        if state != "SUCCEEDED":
            error = body.get("status", {}).get("error") or {}
            raise SourceUnavailable(f"databricks: {step}: statement {state}: {error.get('error_code', '')} "
                                    f"{str(error.get('message', ''))[:300]}".rstrip())
        manifest = body.get("manifest", {})
        columns = [(c["name"], c["type_name"]) for c in manifest.get("schema", {}).get("columns", [])]
        chunk = body.get("result") or {}
        raw_rows = list(chunk.get("data_array") or [])
        while chunk.get("next_chunk_internal_link"):
            chunk = self._call("GET", chunk["next_chunk_internal_link"], step)
            raw_rows.extend(chunk.get("data_array") or [])
        expected = manifest.get("total_row_count")
        if expected is not None and expected != len(raw_rows):
            raise SourceUnavailable(f"databricks: {step}: read {len(raw_rows)} of {expected} rows")
        rows = [[_convert(v, t, n) for v, (n, t) in zip(row, columns)] for row in raw_rows]
        return Result(columns=columns, rows=rows)
