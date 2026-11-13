"""Stage 3 reader: requests, parsing, cache, budget, stop-on-error (ADR-005 §4.2-§4.6, PRD AC-29-AC-36).

The fake transport replays the real S3-1 captures in tests/fixtures/openrouter/. Variants marked
`# synthetic` are derived from a capture by changing one field.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from mapqa.pilot import chain
from mapqa.pilot.openrouter import Exchange
from mapqa.pilot.reader import (CHAT_MODEL, DECISION_MODELS, MODELS, Item, Reader, build_request, cache_key,
                                parse)

from pilot_captures import CAPTURE_FILE, KEY, capture, exchange_of


class FakeTransport:
    """Returns the captured response per model; `override` replaces it for one model."""

    def __init__(self, override: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.override = override or {}

    def post(self, path: str, body: dict) -> Exchange:
        self.calls.append((path, body))
        model = body["model"]
        if model in self.override:
            return copy.deepcopy(self.override[model])
        return exchange_of(CAPTURE_FILE[model])


@pytest.fixture()
def crops(tmp_path) -> tuple[Path, list[Item]]:
    """Three tiny synthetic 'crops' (bytes only matter for hashing)."""
    root = tmp_path / "data"
    (root / "signs" / "crops").mkdir(parents=True)
    items = []
    for i in range(3):
        raw = f"synthetic-jpeg-{i}".encode()  # synthetic
        rel = f"signs/crops/mapillary_{i}.jpg"
        (root / rel).write_bytes(raw)
        items.append(Item(item_id=f"mapillary_{i}", crop=rel, crop_sha256=hashlib.sha256(raw).hexdigest(),
                          sequence=f"seq{i}"))
    return root, items


def make_reader(tmp_path, transport, budget=25) -> Reader:
    return Reader(transport, cache_dir=tmp_path / "cache", log_path=tmp_path / "calls.jsonl", api_key=KEY,
                  budget=budget, run_id="test")


# --- requests (AC-29, AC-30) -------------------------------------------------------------------

@pytest.mark.parametrize("model", MODELS)
def test_request_matches_capture_except_image(model):
    captured = capture(CAPTURE_FILE[model])
    path, body = build_request(model, "IMAGE")
    expected = json.loads(json.dumps(captured["attempts"][0]["request"]).replace(
        "<image/jpeg sha256=7d29ba32bbb103f59dcc8304f36f1b497bfaeb5cda4f1ab38deeadd15dd6e2a3 bytes=44022>", "IMAGE"))
    assert captured["endpoint"].endswith(path)
    assert body == expected


def test_request_body_carries_no_identifier(tmp_path, crops):
    root, items = crops
    transport = FakeTransport()
    make_reader(tmp_path, transport).read_all(items, root, MODELS)
    assert len(transport.calls) == 9
    for _, body in transport.calls:
        text = json.dumps(body)
        for item in items:
            assert item.item_id not in text and item.sequence not in text and "mapillary" not in text


def test_cache_key_covers_model_and_image_not_data_url_bytes():
    _, jev = build_request("typesafe/jev-1.14", "data:image/jpeg;base64,AAAA")
    _, luna = build_request("openai/gpt-6-luna-decisions", "data:image/jpeg;base64,AAAA")
    k1 = cache_key("/api/alpha/decisions", jev, "data:image/jpeg;base64,AAAA", "a" * 64)
    assert k1 != cache_key("/api/alpha/decisions", luna, "data:image/jpeg;base64,AAAA", "a" * 64)
    assert k1 != cache_key("/api/alpha/decisions", jev, "data:image/jpeg;base64,AAAA", "b" * 64)
    assert k1 == cache_key("/api/alpha/decisions", copy.deepcopy(jev), "data:image/jpeg;base64,AAAA", "a" * 64)


# --- parsing (AC-30, AC-35, AC-35b) ------------------------------------------------------------

@pytest.mark.parametrize("model", DECISION_MODELS)
def test_decision_capture_parses_as_answered(model):
    r = parse(model, exchange_of(CAPTURE_FILE[model]))
    assert (r["status"], r["answer"], r["probability"], r["raw_choice"]) == ("answered", "20", 1.0, "20")


def test_chat_capture_parses_as_answered():
    r = parse(CHAT_MODEL, exchange_of("luna_chat.json"))
    assert (r["status"], r["answer"], r["uncertain"]) == ("answered", "20", False)


def _decision_with(probabilities: dict, choice: str) -> Exchange:
    ex = exchange_of("jev_decision.json")
    ans = ex.body["answers"]["speed_limit"]
    ans["probabilities"] = {k: 0 for k in ans["probabilities"]} | probabilities  # synthetic
    ans["choice"] = choice  # synthetic
    return ex


def test_low_probability_is_abstained_not_answered():
    r = parse("typesafe/jev-1.14", _decision_with({"20": 0.6, "30": 0.4}, "20"))
    assert (r["status"], r["answer"], r["raw_choice"], r["probability"]) == ("abstained", None, "20", 0.6)


def test_threshold_is_inclusive():
    assert parse("typesafe/jev-1.14", _decision_with({"20": 0.7, "30": 0.3}, "20"))["status"] == "answered"


def test_unreadable_is_a_model_output_distinct_from_abstained():
    r = parse("typesafe/jev-1.14", _decision_with({"unreadable": 0.9, "30": 0.1}, "unreadable"))
    assert (r["status"], r["answer"]) == ("unreadable", None)


@pytest.mark.parametrize("probs,choice", [
    ({"20": 0.3, "30": 0.7}, "20"),   # choice is not the argmax
    ({"20": 0.5, "30": 0.2}, "20"),   # does not sum to 1
    ({"20": 1.0}, "35"),              # choice outside the options
])
def test_contract_breaks_are_invalid_response(probs, choice):
    assert parse("typesafe/jev-1.14", _decision_with(probs, choice))["status"] == "invalid_response"


def test_missing_answers_is_invalid_response():
    ex = exchange_of("jev_decision.json")
    del ex.body["answers"]  # synthetic
    assert parse("typesafe/jev-1.14", ex)["status"] == "invalid_response"


def _chat_with(content: str, finish_reason: str = "stop") -> Exchange:
    ex = exchange_of("luna_chat.json")
    ex.body["choices"][0]["message"]["content"] = content  # synthetic
    ex.body["choices"][0]["finish_reason"] = finish_reason  # synthetic
    return ex


@pytest.mark.parametrize("content,finish,status", [
    ('{"answer":"30","uncertain":true}', "stop", "uncertain"),
    ('{"answer":"unreadable","uncertain":false}', "stop", "unreadable"),
    ('{"answer":"none","uncertain":false}', "stop", "answered"),
    ('{"answer":"35","uncertain":false}', "stop", "invalid_response"),
    ('{"answer":"30"}', "stop", "invalid_response"),
    ('{"answer":"30","uncertain":"no"}', "stop", "invalid_response"),
    ("thirty", "stop", "invalid_response"),
    ('{"answer":"30","uncertain":false}', "length", "invalid_response"),
])
def test_chat_tier2_rule(content, finish, status):
    assert parse(CHAT_MODEL, _chat_with(content, finish))["status"] == status


def test_http_error_capture_is_model_unavailable_never_an_answer():
    r = parse("typesafe/jev-1.14", exchange_of("error_unknown_model.json"))
    assert (r["status"], r["answer"], r["http_status"]) == ("model_unavailable", None, 400)


def test_timeout_is_model_unavailable():
    r = parse("typesafe/jev-1.14", Exchange(http_status=None, headers={}, body=None, elapsed_s=40.0, error="TimeoutError"))
    assert (r["status"], r["answer"]) == ("model_unavailable", None)


# --- loop: cache, budget, errors, logging (AC-31, AC-32, AC-34, AC-36) ---------------------------

def test_second_run_is_served_from_cache_and_not_counted(tmp_path, crops):
    root, items = crops
    first = FakeTransport()
    make_reader(tmp_path, first).read_all(items, root, MODELS)
    second = FakeTransport()
    reader = make_reader(tmp_path, second)
    readings = reader.read_all(items, root, MODELS)
    assert second.calls == []
    assert all(r.cached for r in readings) and all(r.status == "answered" for r in readings)
    assert reader.calls_made == {m: 0 for m in MODELS}


def test_budget_counts_calls_and_stops_at_limit(tmp_path, crops):
    root, items = crops
    transport = FakeTransport()
    reader = make_reader(tmp_path, transport, budget=2)
    readings = reader.read_all(items, root, ["typesafe/jev-1.14"])
    assert [r.status for r in readings] == ["answered", "answered", "budget_exhausted"]
    assert len(transport.calls) == 2 and reader.calls_made["typesafe/jev-1.14"] == 2


def test_first_error_stops_all_paid_calls(tmp_path, crops):
    root, items = crops
    transport = FakeTransport(override={"typesafe/jev-1.14": exchange_of("error_unknown_model.json")})
    reader = make_reader(tmp_path, transport)
    readings = reader.read_all(items, root, MODELS)
    by_model = {m: [r.status for r in readings if r.model == m] for m in MODELS}
    assert by_model["typesafe/jev-1.14"] == ["model_unavailable", "not_attempted", "not_attempted"]
    assert by_model["openai/gpt-6-luna-decisions"] == ["not_attempted"] * 3
    assert by_model[CHAT_MODEL] == ["not_attempted"] * 3
    assert len(transport.calls) == 1 and reader.calls_made["typesafe/jev-1.14"] == 1
    assert reader.stopped_by_error


def test_errors_are_not_cached(tmp_path, crops):
    root, items = crops
    make_reader(tmp_path, FakeTransport(override={"typesafe/jev-1.14": exchange_of("error_unknown_model.json")})
                ).read_all(items[:1], root, ["typesafe/jev-1.14"])
    retry = FakeTransport()
    readings = make_reader(tmp_path, retry).read_all(items[:1], root, ["typesafe/jev-1.14"])
    assert len(retry.calls) == 1 and readings[0].status == "answered" and not readings[0].cached


def test_cache_hits_still_served_after_an_error(tmp_path, crops):
    root, items = crops
    make_reader(tmp_path, FakeTransport()).read_all(items[1:], root, ["typesafe/jev-1.14"])
    transport = FakeTransport(override={"typesafe/jev-1.14": exchange_of("error_unknown_model.json")})
    readings = make_reader(tmp_path, transport).read_all(items, root, ["typesafe/jev-1.14"])
    assert [r.status for r in readings] == ["model_unavailable", "answered", "answered"]


def test_call_log_records_every_reading_without_secret_or_image(tmp_path, crops):
    root, items = crops
    make_reader(tmp_path, FakeTransport()).read_all(items, root, MODELS)
    lines = (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 9
    rec = json.loads(lines[0])
    assert {"run_id", "model", "item_id", "cache_key", "cached", "status", "http_status", "elapsed_s",
            "generation_id", "cost_usd"} <= rec.keys()
    assert rec["generation_id"].startswith("gen-") and rec["cost_usd"] == pytest.approx(4.326e-05)
    text = (tmp_path / "calls.jsonl").read_text(encoding="utf-8") + "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "cache").glob("*.json"))
    assert KEY not in text and "base64" not in text


def test_key_in_a_record_is_refused(tmp_path, crops):
    root, items = crops
    leaky = exchange_of("jev_decision.json")
    leaky.body["id"] = KEY  # synthetic: a response echoing the key must never be written
    with pytest.raises(RuntimeError, match="secret"):
        make_reader(tmp_path, FakeTransport(override={"typesafe/jev-1.14": leaky})).read_all(
            items[:1], root, ["typesafe/jev-1.14"])
    assert not list((tmp_path / "cache").glob("*.json"))


def test_crop_hash_mismatch_fails_before_any_call(tmp_path, crops):
    root, items = crops
    (root / items[2].crop).write_bytes(b"changed")
    transport = FakeTransport()
    with pytest.raises(ValueError, match="sha256"):
        make_reader(tmp_path, transport).read_all(items, root, MODELS)
    assert transport.calls == []


# --- chain (AC-35a-AC-35d) -----------------------------------------------------------------------

def R(status, answer=None, model="m", item="i"):
    return chain.Step(model=model, item_id=item, status=status, answer=answer)


@pytest.mark.parametrize("t1,t2,expected", [
    (R("answered", "50"), R("answered", "30"), ("50", "decision_model", None)),
    (R("answered", "none"), None, ("none", "decision_model", None)),
    (R("abstained"), R("answered", "30"), ("30", "general_llm", None)),
    (R("abstained"), R("answered", "none"), ("none", "general_llm", None)),
    (R("abstained"), R("uncertain"), (None, "human", "model_unresolved")),
    (R("abstained"), R("unreadable"), (None, "human", "model_unresolved")),
    (R("abstained"), R("invalid_response"), (None, "human", "model_unresolved")),
    (R("abstained"), R("model_unavailable"), (None, "human", "model_unresolved")),
    (R("abstained"), R("budget_exhausted"), (None, "human", "budget_exhausted")),
    (R("unreadable"), R("answered", "30"), (None, "human", "model_unresolved")),
    (R("model_unavailable"), R("answered", "30"), (None, "human", "model_unresolved")),
    (R("budget_exhausted"), None, (None, "human", "budget_exhausted")),
])
def test_chain(t1, t2, expected):
    out = chain.resolve(t1, t2)
    assert (out.answer, out.tier, out.reason) == expected
    assert out.used_tier2 == (t1.status == "abstained")
