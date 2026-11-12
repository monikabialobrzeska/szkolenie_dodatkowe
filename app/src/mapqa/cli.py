"""Command-line interface (ADR-000 §6.1, ADR-001 §5, ADR-003 §5)."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import webbrowser
from pathlib import Path

from mapqa import __version__

EXIT_CODES = """exit codes:
  0  success
  1  unexpected internal problem (bug); traceback on stderr
  2  usage or configuration problem (bad option, invalid TOML, unknown or secret key)
  3  input problem: a required input is missing, unreadable or lacks columns; or no run results for `view`
  4  source problem: source not available yet (databricks, public_api) or unreachable"""

DESCRIPTION = """Speed-limit conflict finder for Krakow map QA (Stage 1).

Compares OSM speed limits with Mapillary speed-limit signs and OSM tags and writes a ranked
review queue of candidates for human review. It never states that the map is wrong.

data sources (--source, env MAPQA_DATA_SOURCE, config [source] kind; default files):
  files       local snapshot folder (implemented)
  databricks  shared workspace.map_qa tables (Stage 2, not available yet)
  public_api  Overture bucket, OSM extract, Mapillary/Panoramax APIs (not available yet)"""


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="TOML configuration file (default: env MAPQA_CONFIG, else built-in defaults)")
    parser.add_argument("--source", choices=["files", "databricks", "public_api"],
                        help="data source; overrides MAPQA_DATA_SOURCE and the config file")
    parser.add_argument("--data-dir", help="folder with the local snapshot (source files)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mapqa", description=DESCRIPTION, epilog=EXIT_CODES,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"mapqa {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{run,check-source,view}")

    run = sub.add_parser("run", help="run all checks and write the review queue, reports and viewer",
                         description="Run all Stage 1 checks and write review_queue.csv/.geojson, summary.md, "
                                     "run_summary.json and viewer.html.", epilog=EXIT_CODES,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_source_options(run)
    run.add_argument("--output-dir", help="output folder (default: reports/map-qa/stage1)")
    run.add_argument("--distance-m", type=float, help="sign-to-road distance limit in metres (default 20)")

    check = sub.add_parser("check-source", help="verify that the selected source and all inputs are usable",
                           epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    _add_source_options(check)

    view = sub.add_parser("view", help="regenerate viewer.html from the last run and open it in the browser",
                          epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    view.add_argument("--output-dir", help="folder with the run results (default: reports/map-qa/stage1)")
    view.add_argument("--no-open", action="store_true", help="only write viewer.html, do not open a browser")
    return parser


def _fail(step: str, message: str, code: int) -> int:
    print(f"error: {step}: {message}", file=sys.stderr)
    return code


def _print_run(result) -> None:
    s = result.summary
    print(f"source: {s['source']['kind']} ({s['source']['location']})")
    print("rows read: " + ", ".join(f"{k} {v['rows']}" for k, v in s["inputs"].items() if k != "snapshot"))
    print(f"candidates: {result.candidates}")
    for key, value in s["counts"]["by_type_state"].items():
        print(f"  {key.replace('|', ' / ')}: {value}")
    print("skipped: " + ", ".join(f"{k} {v}" for k, v in s["counts"]["skipped"].items()))
    print(f"outputs: {result.output_dir}")
    print(f"viewer: {result.outputs['viewer.html']}")


def cmd_run(args) -> int:
    from mapqa.config import ConfigError, load_config
    from mapqa.pipeline import InputsChanged, run, write_failure
    from mapqa.sources.base import InputError, SourceUnavailable

    try:
        config = load_config(config_path=args.config, source=args.source, data_dir=args.data_dir,
                             output_dir=args.output_dir, distance_m=args.distance_m)
    except ConfigError as exc:
        return _fail("config", str(exc), 2)
    try:
        result = run(config)
    except SourceUnavailable as exc:
        write_failure(config, "run", 4, "source", str(exc))
        return _fail("source", str(exc), 4)
    except InputError as exc:
        write_failure(config, "run", 3, "check inputs", str(exc))
        return _fail("check inputs", str(exc), 3)
    except InputsChanged as exc:
        return _fail("verify inputs", str(exc), 1)
    _print_run(result)
    return 0


def cmd_check_source(args) -> int:
    from mapqa.config import ConfigError, load_config
    from mapqa.sources.base import InputError, SourceUnavailable, adapter_for

    try:
        config = load_config(config_path=args.config, source=args.source, data_dir=args.data_dir)
    except ConfigError as exc:
        return _fail("config", str(exc), 2)
    try:
        adapter = adapter_for(config)
        statuses = adapter.check()
    except SourceUnavailable as exc:
        return _fail("source", str(exc), 4)
    except InputError as exc:
        return _fail("check inputs", str(exc), 3)
    print(f"source: {adapter.kind} ({adapter.location()})")
    for status in statuses:
        print(f"  {status.name:<18} {'ok' if status.ok else status.problem}")
    return 0 if all(s.ok for s in statuses) else 3


def cmd_view(args) -> int:
    import pandas as pd

    from mapqa.config import DEFAULT_OUTPUT_DIR
    from mapqa.outputs.viewer import build_viewer_data, write_viewer

    out_dir = Path(args.output_dir).resolve() if args.output_dir else DEFAULT_OUTPUT_DIR
    queue, summary_path = out_dir / "review_queue.geojson", out_dir / "run_summary.json"
    if not queue.is_file() or not summary_path.is_file():
        return _fail("view", f"No run results found — run the pipeline first (looked in {out_dir})", 3)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        features = json.loads(queue.read_text(encoding="utf-8"))["features"]
    except (OSError, ValueError, KeyError) as exc:
        return _fail("view", f"run results in {out_dir} are unreadable ({exc})", 3)
    records = [{**f["properties"], "geometry": f["geometry"]} for f in features]
    data_dir = Path(summary["config"]["data_dir"])
    photos = pd.read_csv(data_dir / "photos.csv", dtype={"photo_id": str}) if (data_dir / "photos.csv").is_file() else None
    path = out_dir / "viewer.html"
    write_viewer(path, build_viewer_data(summary, records, photos, data_dir))
    print(f"viewer: {path}")
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "check-source": cmd_check_source, "view": cmd_view}
    try:
        return handlers[args.command](args)
    except Exception:  # unexpected: report as a bug, exit 1
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
