"""Stage 2 parity against the real Databricks warehouse (ADR-004 §8, PRD AC-05b-AC-05f). Nothing is mocked.

Skipped, with the reason shown, when DATABRICKS_HOST / DATABRICKS_TOKEN / DATABRICKS_SQL_WAREHOUSE_ID are missing.
"""

from __future__ import annotations

import csv
import json
import os

import pytest

from conftest import DATA_DIR
from test_cli import mapqa

REQUIRED_ENV = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_SQL_WAREHOUSE_ID")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.databricks,
    pytest.mark.skipif(not all(os.environ.get(v) for v in REQUIRED_ENV),
                       reason="Databricks variables not set in this session (see .agents/skills/check-databricks)"),
]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    files_out = tmp_path_factory.mktemp("files")
    dbx_out = tmp_path_factory.mktemp("databricks")
    files = mapqa("run", "--source", "files", "--data-dir", str(DATA_DIR), "--output-dir", str(files_out))
    dbx = mapqa("run", "--source", "databricks", "--output-dir", str(dbx_out))
    return files, files_out, dbx, dbx_out


def test_check_source_databricks():
    result = mapqa("check-source", "--source", "databricks")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count(" ok") == 6


def test_queue_is_byte_identical_to_files(runs):
    files, files_out, dbx, dbx_out = runs
    assert files.returncode == 0, files.stderr
    assert dbx.returncode == 0, dbx.stderr
    for name in ("review_queue.csv", "review_queue.geojson"):
        assert (files_out / name).read_bytes() == (dbx_out / name).read_bytes(), name  # AC-05b


def test_run_summary_describes_the_remote_source(runs):
    _, files_out, _, dbx_out = runs
    remote = json.loads((dbx_out / "run_summary.json").read_text(encoding="utf-8"))
    local = json.loads((files_out / "run_summary.json").read_text(encoding="utf-8"))
    assert remote["source"]["kind"] == "databricks"
    assert remote["source"]["location"].startswith("workspace.map_qa")
    assert {k: v["rows"] for k, v in remote["inputs"].items()} == {k: v["rows"] for k, v in local["inputs"].items()}
    for name, item in remote["inputs"].items():
        assert item["fingerprint_before"] == item["fingerprint_after"]
        expected = "sha256:" if name == "snapshot" else "delta-version:"
        assert item["fingerprint_before"].startswith(expected), name
    assert remote["snapshot"] == local["snapshot"]
    assert remote["counts"] == local["counts"]


def test_viewer_of_remote_run_uses_no_local_files(runs):
    _, _, _, dbx_out = runs
    context = json.loads((dbx_out / "viewer_context.json").read_text(encoding="utf-8"))
    assert context["roads"], "road context must come from the Databricks bundle"
    assert all(p["thumb"] is None for p in context["photos"].values())  # offline thumbnails belong to files
    rows = list(csv.DictReader((dbx_out / "review_queue.csv").open(encoding="utf-8")))
    sign_ways = {w for r in rows if r["type"] == "sign_road" for w in r["osm_ids"].split(";")}
    assert sign_ways <= set(context["roads"])


def test_wrong_token_exits_4_without_leaking(tmp_path):
    sentinel = "dapi" + "0" * 32
    result = mapqa("run", "--source", "databricks", "--output-dir", str(tmp_path),
                   env_extra={"DATABRICKS_TOKEN": sentinel})
    assert result.returncode == 4, result.stderr
    assert "credentials rejected" in result.stderr or "request rejected" in result.stderr
    assert sentinel not in result.stdout + result.stderr
    assert not (tmp_path / "review_queue.csv").exists()  # no fallback to files
    failed = (tmp_path / "last_failed_run.json").read_text(encoding="utf-8")
    assert sentinel not in failed
