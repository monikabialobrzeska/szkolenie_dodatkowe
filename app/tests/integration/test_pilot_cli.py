"""Stage 3 CLI: plan / run / evaluate without network (ADR-005 §4.7, §7; PRD AC-33, AC-37).

`run` uses a fake transport that replays the real S3-1 captures (in-process, monkeypatched factory).
The paid run itself is manual QA (S3-5), never part of pytest.
"""

from __future__ import annotations

import json
import shutil

import pytest

from conftest import DATA_DIR
from mapqa import cli
from mapqa.pilot import commands

from pilot_captures import CAPTURE_FILE, KEY, exchange_of

pytestmark = pytest.mark.integration


class Replay:
    def __init__(self):
        self.calls = []

    def post(self, path, body):
        self.calls.append(body["model"])
        return exchange_of(CAPTURE_FILE[body["model"]])


@pytest.fixture()
def planned(tmp_path, capsys):
    out = tmp_path / "pilot"
    assert cli.main(["pilot", "plan", "--output-dir", str(out)]) == 0
    printed = capsys.readouterr().out
    code = commands.confirm_code(out / "manifest.json")
    return out, code, printed


def test_plan_writes_label_free_manifest_and_prints_code(planned):
    out, code, printed = planned
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["items"]) == 20 and manifest["budget_per_model"] == 25 and manifest["calls_planned"] == 60
    text = (out / "manifest.json").read_text(encoding="utf-8")
    for word in ("sign_value", "negative_type", "mapillary_value", "stratum", "label"):
        assert word not in text
    assert len(code) == 12 and (out / "strata.json").exists()
    assert code in printed and "60" in printed


def test_plan_is_reproducible(planned, tmp_path):
    out, code, _ = planned
    assert cli.main(["pilot", "plan", "--output-dir", str(tmp_path / "again")]) == 0
    assert commands.confirm_code(tmp_path / "again" / "manifest.json") == code


def test_run_without_matching_code_makes_no_call(planned, monkeypatch):
    out, code, _ = planned
    replay = Replay()
    monkeypatch.setattr(commands, "make_transport", lambda key: replay)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    assert cli.main(["pilot", "run", "--output-dir", str(out)]) == 2
    assert cli.main(["pilot", "run", "--confirm", "000000000000", "--output-dir", str(out)]) == 2
    assert replay.calls == []


def test_run_without_key_is_exit_4(planned, monkeypatch):
    out, code, _ = planned
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert cli.main(["pilot", "run", "--confirm", code, "--output-dir", str(out)]) == 4


def test_run_reads_no_labels_and_evaluate_writes_report(planned, tmp_path, monkeypatch, capsys):
    out, code, _ = planned
    # data dir with crops only: no labels.csv, no signs.csv -> the reading step must still work (AC-37)
    data = tmp_path / "crops_only"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["items"]:
        (data / item["crop"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DATA_DIR / item["crop"], data / item["crop"])
    replay = Replay()
    monkeypatch.setattr(commands, "make_transport", lambda key: replay)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    assert cli.main(["pilot", "run", "--confirm", code, "--data-dir", str(data), "--output-dir", str(out)]) == 0
    assert len(replay.calls) == 60
    readings = json.loads((out / "readings.json").read_text(encoding="utf-8"))
    assert len(readings) == 60 and {r["status"] for r in readings} == {"answered"}
    capsys.readouterr()

    # second run: everything from cache, no calls (AC-34)
    replay.calls.clear()
    assert cli.main(["pilot", "run", "--confirm", code, "--data-dir", str(data), "--output-dir", str(out)]) == 0
    assert replay.calls == []
    assert "calls made: typesafe/jev-1.14 0" in capsys.readouterr().out

    assert cli.main(["pilot", "evaluate", "--output-dir", str(out)]) == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    metrics = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert metrics["certain_items"] == 20
    assert "/ 20" in report and "0.7" in report and "working reference" in report
    assert "Mapillary detector" in report and "optimistic" in report
    assert KEY not in report and KEY not in (out / "calls.jsonl").read_text(encoding="utf-8")


def test_evaluate_without_readings_is_exit_3(planned):
    out, _, _ = planned
    assert cli.main(["pilot", "evaluate", "--output-dir", str(out)]) == 3
