"""Shared loader for the real S3-1 OpenRouter captures (tests/fixtures/openrouter/)."""

from __future__ import annotations

import json
from pathlib import Path

from mapqa.pilot.openrouter import Exchange

CAPTURES = Path(__file__).resolve().parent / "fixtures" / "openrouter"
CAPTURE_FILE = {"typesafe/jev-1.14": "jev_decision.json", "openai/gpt-6-luna-decisions": "luna_decisions_decision.json",
                "openai/gpt-6-luna": "luna_chat.json"}
KEY = "sk-or-v1-" + "0" * 64  # synthetic sentinel, never a real key


def capture(name: str) -> dict:
    return json.loads((CAPTURES / name).read_text(encoding="utf-8"))


def exchange_of(name: str) -> Exchange:
    a = capture(name)["attempts"][0]
    return Exchange(http_status=a["http_status"], headers=a["headers"], body=a["body"], elapsed_s=a["elapsed_s"])
