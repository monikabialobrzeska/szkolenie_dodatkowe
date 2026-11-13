"""Real CLI on the real pinned snapshot (ADR-000 §10, ADR-001 §8, ADR-003 §8). Nothing is mocked."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from conftest import DATA_DIR, REPO_ROOT

pytestmark = pytest.mark.integration
INPUTS = ["osm_roads.parquet", "overture_segments.parquet", "photos.csv", "photo_road_links.csv",
          "traffic_signs.geojson", "sources.json"]


def mapqa(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the real CLI. A None value in env_extra removes that variable."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MAPQA_")}
    env["PYTHONIOENCODING"] = "utf-8"
    for key, value in (env_extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run([sys.executable, "-m", "mapqa", *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, cwd=REPO_ROOT, timeout=600)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("run1")
    before = {name: sha(DATA_DIR / name) for name in INPUTS}
    # CLI option must win over the environment variable (precedence, ADR-001 §3.3)
    result = mapqa("run", "--source", "files", "--data-dir", str(DATA_DIR), "--output-dir", str(out),
                   env_extra={"MAPQA_DATA_SOURCE": "databricks"})
    after = {name: sha(DATA_DIR / name) for name in INPUTS}
    return result, out, before, after


def test_help_lists_sources_and_exit_codes():
    result = mapqa("--help")
    assert result.returncode == 0
    for text in ("files", "databricks", "public_api", "run", "check-source", "view", "exit codes"):
        assert text in result.stdout
    assert mapqa("run", "--help").returncode == 0


def test_full_run_outputs_and_counts(full_run):
    result, out, before, after = full_run
    assert result.returncode == 0, result.stderr
    for name in ("review_queue.csv", "review_queue.geojson", "summary.md", "run_summary.json", "viewer.html",
                 "viewer_context.json"):
        assert (out / name).is_file(), name
    assert before == after  # AC-01

    summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["config"]["source_kind_from"] == "cli"
    assert summary["source"]["kind"] == "files"
    assert {k: v["rows"] for k, v in summary["inputs"].items() if k != "snapshot"} == {
        "osm_roads": 99768, "overture_segments": 85405, "photos": 561, "photo_road_links": 561, "traffic_signs": 1730}
    assert all(v["sha256_before"] == v["sha256_after"] for v in summary["inputs"].values())

    rows = list(csv.DictReader((out / "review_queue.csv").open(encoding="utf-8", newline="")))
    by_type_state = Counter(f"{r['type']}|{r['state']}" for r in rows)
    assert by_type_state == Counter(summary["counts"]["by_type_state"])  # TAC-05
    assert [int(r["rank"]) for r in rows] == list(range(1, len(rows) + 1))
    assert len({r["candidate_id"] for r in rows}) == len(rows)

    # TAC-002-02 / TAC-002-04 order-of-magnitude checks
    types = Counter(r["type"] for r in rows)
    reasons = Counter(r["reason_code"] for r in rows)
    assert not any(r["type"] == "sign_road" and r["state"] == "conflict" for r in rows)
    assert 250 <= types["missing_limit"] <= 330
    assert types["unparseable_value"] == 0
    assert 140 <= types["tag_inconsistency"] <= 180
    assert 170 <= reasons["stale_sign"] <= 210
    single_road_signs = sum(1 for r in rows if r["type"] == "sign_road" and r["reason_code"] != "multiple_candidate_roads")
    single_road_signs += summary["counts"]["skipped"].get("sign_matches_road", 0)
    assert 380 <= single_road_signs <= 460
    assert all("error" not in r["explanation"].lower() for r in rows)

    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "OpenStreetMap contributors" in md and "2026-10-21.0" in md

    viewer = (out / "viewer.html").read_text(encoding="utf-8")
    assert viewer.count('"candidate_id"') == len(rows)  # TAC-003-02
    payload = json.loads(viewer.split('id="mapqa-data">', 1)[1].split("</script>", 1)[0])
    sign_ways = {w for r in rows if r["type"] == "sign_road" for w in r["osm_ids"].split(";")}
    assert sign_ways <= set(payload["roads"])  # PRD 9.3: the road a sign is compared with is drawable
    assert payload["roads"][next(iter(sign_ways))]["type"] == "LineString"


def test_second_run_is_byte_identical(full_run, tmp_path):
    _, out1, _, _ = full_run
    result = mapqa("run", "--data-dir", str(DATA_DIR), "--output-dir", str(tmp_path))
    assert result.returncode == 0, result.stderr
    for name in ("review_queue.csv", "review_queue.geojson"):
        assert (out1 / name).read_bytes() == (tmp_path / name).read_bytes(), name  # AC-03


def test_missing_input_exits_3_without_queue(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name in INPUTS:
        if name != "photos.csv":
            shutil.copy2(DATA_DIR / name, data / name)
    out = tmp_path / "out"
    result = mapqa("run", "--data-dir", str(data), "--output-dir", str(out))
    assert result.returncode == 3
    assert "photos.csv" in result.stderr
    assert not (out / "review_queue.csv").exists()


def test_unavailable_sources_exit_4(tmp_path):
    no_databricks = {"DATABRICKS_HOST": None, "DATABRICKS_TOKEN": None, "DATABRICKS_SQL_WAREHOUSE_ID": None}
    for command in ("run", "check-source"):
        out = ["--output-dir", str(tmp_path)] if command == "run" else []
        result = mapqa(command, "--source", "public_api", *out)
        assert result.returncode == 4 and "not available yet" in result.stderr, (command, result.stderr)
        # databricks without credentials: source error naming the missing variable, never a fallback to files
        result = mapqa(command, "--source", "databricks", *out, env_extra=no_databricks)
        assert result.returncode == 4 and "missing DATABRICKS_" in result.stderr, (command, result.stderr)
        assert not (tmp_path / "review_queue.csv").exists()


def test_check_source_files_ok():
    result = mapqa("check-source", "--data-dir", str(DATA_DIR))
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(" ok") >= 6


def test_config_errors(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("[checks]\nsign_road_distanse_m = 20\n", encoding="utf-8")
    result = mapqa("run", "--config", str(bad), "--output-dir", str(tmp_path / "o"))
    assert result.returncode == 2 and "sign_road_distanse_m" in result.stderr

    secret = tmp_path / "secret.toml"
    secret.write_text('[source.databricks]\ntoken = "sentinel-value-123"\n', encoding="utf-8")
    result = mapqa("run", "--config", str(secret), "--output-dir", str(tmp_path / "o"))
    assert result.returncode == 2
    assert "sentinel-value-123" not in result.stderr + result.stdout

    good = tmp_path / "good.toml"
    good.write_text("[source]\nkind = \"files\"\n[checks]\nsign_road_distance_m = 15.0\n", encoding="utf-8")
    result = mapqa("check-source", "--config", str(good), "--data-dir", str(DATA_DIR))
    assert result.returncode == 0, result.stderr


def test_view_without_results_exits_3(tmp_path):
    result = mapqa("view", "--output-dir", str(tmp_path), "--no-open")
    assert result.returncode == 3
    assert "No run results found" in result.stderr


def test_view_regenerates_viewer(full_run):
    _, out, _, _ = full_run
    (out / "viewer.html").unlink()
    result = mapqa("view", "--output-dir", str(out), "--no-open")
    assert result.returncode == 0, result.stderr
    viewer = (out / "viewer.html").read_text(encoding="utf-8")
    payload = json.loads(viewer.split('id="mapqa-data">', 1)[1].split("</script>", 1)[0])
    assert payload["roads"] and payload["candidates"]
