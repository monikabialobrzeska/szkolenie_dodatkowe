"""viewer.html — read-only result viewer (ADR-003)."""

from __future__ import annotations

import json
import math
from importlib import resources
from pathlib import Path
from typing import Any

import pandas as pd

PLACEHOLDER = "__MAPQA_DATA__"


def _clean(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def render_viewer(data: dict[str, Any]) -> str:
    template = resources.files("mapqa.outputs").joinpath("viewer_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return template.replace(PLACEHOLDER, payload)


def photo_lookup(photos: pd.DataFrame | None, photo_ids: set[str], data_dir: Path) -> dict[str, dict[str, Any]]:
    """Metadata of referenced photos; `thumb` is a file:// URI of an offline copy when it exists."""
    if photos is None or not photo_ids:
        return {}
    out = {}
    offline = data_dir / "offline"
    for row in photos[photos["photo_id"].isin(photo_ids)].to_dict("records"):
        thumb_path = offline / str(row.get("filename"))
        captured = row.get("captured_at")
        out[str(row["photo_id"])] = {
            "captured_at": None if captured is None or pd.isna(captured) else pd.Timestamp(captured).isoformat(),
            "author": _clean(row.get("author")),
            "license": _clean(row.get("license")),
            "source": _clean(row.get("source")),
            "source_url": _clean(row.get("source_url")),
            "thumb": thumb_path.resolve().as_uri() if thumb_path.is_file() else None,
        }
    return out


def build_viewer_data(summary: dict[str, Any], records: list[dict[str, Any]], photos: pd.DataFrame | None,
                      data_dir: Path) -> dict[str, Any]:
    referenced: set[str] = set()
    for rec in records:
        ids = rec.get("photo_ids") or []
        referenced.update(ids if isinstance(ids, list) else str(ids).split(";"))
    return {
        "summary": {key: summary[key] for key in ("source", "snapshot", "inputs", "counts", "config", "run_at")},
        "candidates": records,
        "photos": photo_lookup(photos, referenced, data_dir),
    }


def write_viewer(path: Path, data: dict[str, Any]) -> None:
    path.write_text(render_viewer(data), encoding="utf-8", newline="\n")
