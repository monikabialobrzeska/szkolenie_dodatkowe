"""Reading step (ADR-005 §4.2-§4.6, PRD AC-29-AC-36). Reads only the manifest and crop bytes, never labels (AC-37)."""

from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from mapqa.pilot.openrouter import Exchange, Transport

DECISION_MODELS = ("typesafe/jev-1.14", "openai/gpt-6-luna-decisions")
CHAT_MODEL = "openai/gpt-6-luna"
MODELS = (*DECISION_MODELS, CHAT_MODEL)
DECISIONS_PATH = "/api/alpha/decisions"
CHAT_PATH = "/api/v1/chat/completions"
THRESHOLD = 0.7  # fixed before the run, not tuned (ADR-005 §5)
BUDGET_PER_MODEL = 25  # AC-31

VALUES = ["5", "10", "15", "20", "25", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120", "130", "140"]
OPTIONS = [*VALUES, "none", "unreadable"]
QUESTION = "What speed limit is shown on a round speed-limit sign (white disc with a red border) in this image?"
CRITERIA = {v: f"The sign shows {v} km/h." for v in VALUES}
CRITERIA["none"] = ("There is no round speed-limit sign with a number in the image, including zone signs, "
                    "zone-end and end-of-limit signs.")
CRITERIA["unreadable"] = "A round speed-limit sign is present but its number cannot be read reliably."
CHAT_TEXT = (f"{QUESTION}\nAnswer with exactly one of: " + ", ".join(f'"{v}"' for v in OPTIONS)
             + ". Use \"none\" if there is no such sign (also for zone, zone-end and end-of-limit signs) and "
               "\"unreadable\" if the number cannot be read. Set uncertain to true if you are not sure.")
CHAT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "uncertain"],
               "properties": {"answer": {"type": "string", "enum": OPTIONS}, "uncertain": {"type": "boolean"}}}


@dataclass(frozen=True)
class Item:
    item_id: str
    crop: str  # relative to the data dir
    crop_sha256: str
    sequence: str


@dataclass
class Reading:
    model: str
    item_id: str
    status: str
    answer: str | None = None
    probability: float | None = None
    uncertain: bool | None = None
    raw_choice: str | None = None
    http_status: int | None = None
    elapsed_s: float | None = None
    generation_id: str | None = None
    cost_usd: float | None = None
    cached: bool = False
    cache_key: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def data_url(raw: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode()


def build_request(model: str, image_url: str) -> tuple[str, dict]:
    image_part = {"type": "image_url", "image_url": {"url": image_url}}
    if model in DECISION_MODELS:
        return DECISIONS_PATH, {"model": model, "state": [image_part], "questions": {"speed_limit": {
            "type": "choice", "instructions": QUESTION, "criteria": dict(CRITERIA)}}}
    if model == CHAT_MODEL:
        return CHAT_PATH, {"model": model, "reasoning": {"effort": "medium"},
                           "response_format": {"type": "json_schema", "json_schema": {
                               "name": "sign_reading", "strict": True, "schema": CHAT_SCHEMA}},
                           "messages": [{"role": "user", "content": [{"type": "text", "text": CHAT_TEXT},
                                                                     image_part]}]}
    raise ValueError(f"model not in the pilot: {model}")


def cache_key(path: str, body: dict, image_url: str, crop_sha256: str) -> str:
    canonical = json.dumps({"endpoint": path, "body": body}, sort_keys=True, separators=(",", ":"))
    canonical = canonical.replace(image_url, f"sha256:{crop_sha256}")
    return hashlib.sha256(canonical.encode()).hexdigest()


def _meta(ex: Exchange) -> dict:
    body = ex.body if isinstance(ex.body, dict) else {}
    usage = body.get("usage") or {}
    cost = (usage.get("cost_details") or {}).get("upstream_inference_cost", usage.get("cost"))
    gen = body.get("id") if isinstance(body.get("id"), str) else None
    return {"http_status": ex.http_status, "elapsed_s": ex.elapsed_s,
            "generation_id": gen or (ex.headers or {}).get("x-generation-id"),
            "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None}


def _result(meta: dict, status: str, answer=None, probability=None, uncertain=None, raw_choice=None) -> dict:
    return {"status": status, "answer": answer, "probability": probability, "uncertain": uncertain,
            "raw_choice": raw_choice, **meta}


def _parse_decision(ex: Exchange, meta: dict) -> dict:
    try:
        ans = ex.body["answers"]["speed_limit"]
        choice, probs = ans["choice"], ans["probabilities"]
    except (KeyError, TypeError):
        return _result(meta, "invalid_response")
    if (not isinstance(probs, dict) or set(probs) != set(OPTIONS) or choice not in OPTIONS
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in probs.values())):
        return _result(meta, "invalid_response")
    top = max(probs.values())
    if not math.isclose(sum(probs.values()), 1.0, abs_tol=0.01) or probs[choice] != top:
        return _result(meta, "invalid_response")
    p = float(top)
    if p < THRESHOLD:
        return _result(meta, "abstained", probability=p, raw_choice=choice)
    if choice == "unreadable":
        return _result(meta, "unreadable", probability=p, raw_choice=choice)
    return _result(meta, "answered", answer=choice, probability=p, raw_choice=choice)


def _parse_chat(ex: Exchange, meta: dict) -> dict:
    try:
        first = ex.body["choices"][0]
        finish = first["finish_reason"]
        content = json.loads(first["message"]["content"])
    except (KeyError, TypeError, IndexError, ValueError):
        return _result(meta, "invalid_response")
    if (finish != "stop" or not isinstance(content, dict) or set(content) != {"answer", "uncertain"}
            or content["answer"] not in OPTIONS or not isinstance(content["uncertain"], bool)):
        return _result(meta, "invalid_response")
    answer, uncertain = content["answer"], content["uncertain"]
    if answer == "unreadable":
        return _result(meta, "unreadable", uncertain=uncertain, raw_choice=answer)
    if uncertain:
        return _result(meta, "uncertain", uncertain=True, raw_choice=answer)
    return _result(meta, "answered", answer=answer, uncertain=False, raw_choice=answer)


def parse(model: str, ex: Exchange) -> dict:
    """Exchange -> reading fields. Anything but a valid HTTP 200 answer is never an answer (AC-32)."""
    meta = _meta(ex)
    if ex.http_status != 200 or ex.error:
        return _result(meta, "model_unavailable")
    return _parse_decision(ex, meta) if model in DECISION_MODELS else _parse_chat(ex, meta)


class Reader:
    """Paid loop: cache first, then budget, then one call; after the first error no further paid calls."""

    def __init__(self, transport: Transport, *, cache_dir: Path, log_path: Path, api_key: str,
                 budget: int = BUDGET_PER_MODEL, run_id: str):
        self.transport = transport
        self.cache_dir = Path(cache_dir)
        self.log_path = Path(log_path)
        self._secret = api_key
        self.budget = budget
        self.run_id = run_id
        self.calls_made: dict[str, int] = {}
        self.stopped_by_error = False

    def _write(self, path: Path, text: str, append: bool = False) -> None:
        if self._secret and self._secret in text:
            raise RuntimeError("secret found in a record; nothing written")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a" if append else "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    @staticmethod
    def load_crops(items: list[Item], data_dir: Path) -> dict[str, bytes]:
        crops = {}
        for item in items:
            raw = (Path(data_dir) / item.crop).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item.crop_sha256:
                raise ValueError(f"crop sha256 mismatch for {item.item_id}; nothing sent")
            crops[item.item_id] = raw
        return crops

    def read_one(self, model: str, item: Item, raw: bytes) -> Reading:
        url = data_url(raw)
        path, body = build_request(model, url)
        key = cache_key(path, body, url, item.crop_sha256)
        cached_file = self.cache_dir / f"{key}.json"
        if cached_file.exists():
            c = json.loads(cached_file.read_text(encoding="utf-8"))
            ex = Exchange(c["http_status"], c.get("headers") or {}, c["body"], c.get("elapsed_s", 0.0))
            return Reading(model, item.item_id, cached=True, cache_key=key, **parse(model, ex))
        if self.stopped_by_error:
            return Reading(model, item.item_id, "not_attempted", cache_key=key)
        if self.calls_made.get(model, 0) >= self.budget:
            return Reading(model, item.item_id, "budget_exhausted", cache_key=key)
        self.calls_made[model] = self.calls_made.get(model, 0) + 1
        ex = self.transport.post(path, body)
        reading = Reading(model, item.item_id, cached=False, cache_key=key, **parse(model, ex))
        if reading.status == "model_unavailable":
            self.stopped_by_error = True  # AC-32: errors are not cached and stop the paid loop
        else:
            record = {"http_status": ex.http_status, "headers": ex.headers, "elapsed_s": ex.elapsed_s, "body": ex.body}
            self._write(cached_file, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
        return reading

    def read_all(self, items: list[Item], data_dir: Path, models) -> list[Reading]:
        crops = self.load_crops(items, data_dir)  # fail before any call
        readings = []
        for model in models:
            self.calls_made.setdefault(model, 0)
            for item in items:
                reading = self.read_one(model, item, crops[item.item_id])
                log = {"run_id": self.run_id, "model": model, "item_id": item.item_id,
                       "cache_key": reading.cache_key, "cached": reading.cached, "status": reading.status,
                       "http_status": reading.http_status, "elapsed_s": reading.elapsed_s,
                       "generation_id": reading.generation_id, "cost_usd": reading.cost_usd}
                self._write(self.log_path, json.dumps(log, ensure_ascii=False) + "\n", append=True)
                readings.append(reading)
        return readings
