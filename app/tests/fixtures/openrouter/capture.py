"""Capture real OpenRouter responses for Stage 3 fixtures (AGENTS.md mock rule; PRD AC-29, AC-32, AC-33, AC-36).

PAID CALLS: run only after the user confirmed the manifest. Sends only the crop bytes and a neutral question
(no file name, photo id, label, Mapillary value or map attribute). No retries; 40 s timeout; at most
MAX_ATTEMPTS requests per model (a 400 is rejected before reaching the model and costs nothing).
Saved files replace the image with its SHA-256 and size and are checked for the API key before the script exits.

Run from app/:  uv run python tests/fixtures/openrouter/capture.py <crop.jpg> <case> [<case> ...]
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
BASE = "https://openrouter.ai"
TIMEOUT_S = 40
MAX_ATTEMPTS = 3
VALUES = ["5", "10", "15", "20", "25", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120", "130", "140"]
QUESTION = "What speed limit is shown on a round speed-limit sign (white disc with a red border) in this image?"
CRITERIA = {v: f"The sign shows {v} km/h." for v in VALUES}
CRITERIA["none"] = ("There is no round speed-limit sign with a number in the image, including zone signs, "
                    "zone-end and end-of-limit signs.")
CRITERIA["unreadable"] = "A round speed-limit sign is present but its number cannot be read reliably."
KEEP_HEADERS = ("content-type", "x-generation-id", "x-request-id")


def key() -> str:
    value = os.environ.get("OPENROUTER_API_KEY", "")
    if not value.startswith("sk-or-"):
        raise SystemExit("OPENROUTER_API_KEY missing or malformed (shape check only)")
    return value


def decisions_body(model: str, data_url: str, state_shape: str) -> dict:
    image_part = {"type": "image_url", "image_url": {"url": data_url}}
    state = [image_part] if state_shape == "parts" else {"image": image_part}
    return {"model": model, "state": state,
            "questions": {"speed_limit": {"type": "choice", "instructions": QUESTION, "criteria": CRITERIA}}}


def chat_body(model: str, data_url: str) -> dict:
    options = ", ".join(f'"{v}"' for v in [*VALUES, "none", "unreadable"])
    text = (f"{QUESTION}\nAnswer with exactly one of: {options}. Use \"none\" if there is no such sign "
            "(also for zone, zone-end and end-of-limit signs) and \"unreadable\" if the number cannot be read. "
            "Set uncertain to true if you are not sure.")
    schema = {"type": "object", "additionalProperties": False, "required": ["answer", "uncertain"],
              "properties": {"answer": {"type": "string", "enum": [*VALUES, "none", "unreadable"]},
                             "uncertain": {"type": "boolean"}}}
    return {"model": model, "reasoning": {"effort": "medium"},
            "response_format": {"type": "json_schema", "json_schema": {"name": "sign_reading", "strict": True, "schema": schema}},
            "messages": [{"role": "user", "content": [{"type": "text", "text": text},
                                                      {"type": "image_url", "image_url": {"url": data_url}}]}]}


CASES = {
    "jev_decision": ("/api/alpha/decisions", "typesafe/jev-1.14", "decisions"),
    "luna_decisions_decision": ("/api/alpha/decisions", "openai/gpt-6-luna-decisions", "decisions"),
    "luna_chat": ("/api/v1/chat/completions", "openai/gpt-6-luna", "chat"),
    # free error case: an unknown model id is rejected by OpenRouter before any model runs
    "error_unknown_model": ("/api/alpha/decisions", "mapqa-invalid/does-not-exist", "decisions"),
}


def post(path: str, body: dict, api_key: str) -> dict:
    req = Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                           "X-Title": "mapqa course pilot"})
    started = time.monotonic()
    try:
        with urlopen(req, timeout=TIMEOUT_S) as resp:
            status, headers, raw = resp.status, resp.headers, resp.read()
    except HTTPError as exc:
        status, headers, raw = exc.code, exc.headers, exc.read()
    except (URLError, TimeoutError) as exc:
        return {"http_status": None, "error": type(exc).__name__, "elapsed_s": round(time.monotonic() - started, 2)}
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    return {"http_status": status, "headers": {k: headers.get(k) for k in KEEP_HEADERS if headers.get(k)},
            "elapsed_s": round(time.monotonic() - started, 2), "body": parsed}


def scrub_image(body: dict, data_url: str, placeholder: str) -> dict:
    return json.loads(json.dumps(body).replace(data_url, placeholder))


def main() -> None:
    crop, names = Path(sys.argv[1]), sys.argv[2:]
    api_key = key()
    raw = crop.read_bytes()
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    placeholder = f"<image/jpeg sha256={hashlib.sha256(raw).hexdigest()} bytes={len(raw)}>"
    written = []
    for name in names:
        path_, model, kind = CASES[name]
        shapes = ["parts", "object"] if kind == "decisions" else [None]
        attempts = []
        for shape in shapes[:MAX_ATTEMPTS]:
            body = decisions_body(model, data_url, shape) if kind == "decisions" else chat_body(model, data_url)
            exchange = post(path_, body, api_key)
            exchange["request"] = scrub_image(body, data_url, placeholder)
            exchange["state_shape"] = shape
            attempts.append(exchange)
            if exchange.get("http_status") != 400 or name.startswith("error_"):
                break  # success or a real error: never retry (AC-32)
        record = {"case": name, "model": model, "endpoint": BASE + path_,
                  "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "sent": "crop bytes + neutral question only (no file name, id, label, Mapillary value)",
                  "attempts": attempts}
        out = HERE / f"{name}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        written.append(out)
        print(name, [(a.get("http_status"), a.get("state_shape"), a.get("elapsed_s")) for a in attempts])
    leaked = [p.name for p in written if api_key in p.read_text(encoding="utf-8")]
    if leaked:
        for p in written:
            p.unlink()
        raise SystemExit(f"API key found in {leaked}; captures deleted")
    print("key sentinel check: clean")


if __name__ == "__main__":
    main()
