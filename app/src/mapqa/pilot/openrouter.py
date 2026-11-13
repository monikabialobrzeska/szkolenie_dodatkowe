"""Minimal OpenRouter transport (ADR-005 §3, §4.6): one POST, 40 s timeout, no retries, no new dependency."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://openrouter.ai"
TIMEOUT_S = 40
KEEP_HEADERS = ("content-type", "x-generation-id", "x-request-id")


class ApiKeyError(Exception):
    """OPENROUTER_API_KEY missing or malformed (shape check only)."""


@dataclass
class Exchange:
    http_status: int | None
    headers: dict = field(default_factory=dict)
    body: Any = None
    elapsed_s: float = 0.0
    error: str | None = None


class Transport(Protocol):
    def post(self, path: str, body: dict) -> Exchange: ...


def api_key_from_env() -> str:
    value = os.environ.get("OPENROUTER_API_KEY", "")
    if not value.startswith("sk-or-") or len(value) < 20:
        raise ApiKeyError("OPENROUTER_API_KEY missing or malformed (shape check only)")
    return value


class HttpTransport:
    def __init__(self, api_key: str, base_url: str = BASE_URL, timeout_s: float = TIMEOUT_S):
        self._key = api_key
        self._base = base_url
        self._timeout = timeout_s

    def post(self, path: str, body: dict) -> Exchange:
        req = Request(self._base + path, data=json.dumps(body).encode(), method="POST",
                      headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                               "X-Title": "mapqa course pilot"})
        started = time.monotonic()
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                status, headers, raw = resp.status, resp.headers, resp.read()
        except HTTPError as exc:
            status, headers, raw = exc.code, exc.headers, exc.read()
        except (URLError, TimeoutError, OSError) as exc:
            return Exchange(None, {}, None, round(time.monotonic() - started, 2), type(exc).__name__)
        text = raw.decode("utf-8", "replace")
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            parsed = text
        return Exchange(status, {k: headers.get(k) for k in KEEP_HEADERS if headers.get(k)}, parsed,
                        round(time.monotonic() - started, 2))
