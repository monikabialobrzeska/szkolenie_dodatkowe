"""Configuration: TOML file + environment + CLI with strict keys (ADR-001 §3)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

SOURCES = ("files", "databricks", "public_api")
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "course-materials" / "exercise-data" / "map-qa"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "map-qa" / "stage1"
DEFAULT_MAIN_CLASSES = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
                        "secondary", "secondary_link")

ALLOWED_KEYS: dict[str, set[str]] = {
    "": {"source", "area", "checks", "output"},
    "source": {"kind", "files", "databricks"},
    "source.files": {"data_dir"},
    "source.databricks": {"host", "warehouse_id", "schema"},
    "area": {"bbox"},
    "checks": {"sign_road_distance_m", "main_road_classes", "association_excluded_classes"},
    "output": {"dir"},
}
SECRET_WORDS = ("token", "password", "secret")


class ConfigError(Exception):
    """Invalid configuration (exit code 2)."""


@dataclass(frozen=True)
class RunConfig:
    source_kind: str = "files"
    source_kind_from: str = "default"
    data_dir: Path = DEFAULT_DATA_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    bbox: tuple[float, float, float, float] = (19.80, 49.95, 20.15, 50.15)
    sign_road_distance_m: float = 20.0
    main_road_classes: tuple[str, ...] = DEFAULT_MAIN_CLASSES
    association_excluded_classes: tuple[str, ...] = ("service",)
    databricks: dict[str, str] = field(default_factory=dict)
    config_file: Path | None = None

    def public_dict(self) -> dict[str, Any]:
        """Resolved configuration without secrets, for run_summary.json."""
        return {
            "source_kind": self.source_kind,
            "source_kind_from": self.source_kind_from,
            "data_dir": str(self.data_dir),
            "output_dir": str(self.output_dir),
            "bbox": list(self.bbox),
            "sign_road_distance_m": self.sign_road_distance_m,
            "main_road_classes": list(self.main_road_classes),
            "association_excluded_classes": list(self.association_excluded_classes),
            "config_file": str(self.config_file) if self.config_file else None,
        }


def _walk(table: Mapping[str, Any], prefix: str = "") -> None:
    allowed = ALLOWED_KEYS.get(prefix)
    for key, value in table.items():
        dotted = f"{prefix}.{key}" if prefix else key
        lowered = key.lower()
        if any(word in lowered for word in SECRET_WORDS) or lowered.endswith("_key"):
            raise ConfigError(f"config key '{dotted}': secrets must be set in the environment, not in the config file")
        if allowed is None or key not in allowed:
            raise ConfigError(f"unknown config key '{dotted}'")
        if isinstance(value, dict):
            _walk(value, dotted)


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_config(*, config_path: str | None = None, source: str | None = None, data_dir: str | None = None,
                output_dir: str | None = None, distance_m: float | None = None,
                env: Mapping[str, str] | None = None) -> RunConfig:
    env = os.environ if env is None else env
    file_data: dict[str, Any] = {}
    config_file: Path | None = None
    path_text = config_path or env.get("MAPQA_CONFIG")
    if path_text:
        config_file = Path(path_text).resolve()
        if not config_file.is_file():
            raise ConfigError(f"config file not found: {config_file}")
        try:
            file_data = tomllib.loads(config_file.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {config_file}: {exc}") from None
        _walk(file_data)
    base = config_file.parent if config_file else Path.cwd()

    src = file_data.get("source", {})
    checks = file_data.get("checks", {})
    if source:
        kind, kind_from = source, "cli"
    elif env.get("MAPQA_DATA_SOURCE"):
        kind, kind_from = env["MAPQA_DATA_SOURCE"], "env"
    elif "kind" in src:
        kind, kind_from = src["kind"], "file"
    else:
        kind, kind_from = "files", "default"
    if kind not in SOURCES:
        raise ConfigError(f"unknown source '{kind}' (choose one of: {', '.join(SOURCES)})")

    resolved_data = DEFAULT_DATA_DIR
    if data_dir:
        resolved_data = Path(data_dir).resolve()
    elif "data_dir" in src.get("files", {}):
        resolved_data = _resolve(base, src["files"]["data_dir"])

    resolved_out = DEFAULT_OUTPUT_DIR
    if output_dir:
        resolved_out = Path(output_dir).resolve()
    elif "dir" in file_data.get("output", {}):
        resolved_out = _resolve(base, file_data["output"]["dir"])
    if resolved_out == resolved_data or resolved_data in resolved_out.parents:
        raise ConfigError(f"output folder must not be inside the data folder: {resolved_out}")

    distance = distance_m if distance_m is not None else checks.get("sign_road_distance_m", 20.0)
    if not isinstance(distance, (int, float)) or not 1.0 <= float(distance) <= 100.0:
        raise ConfigError("checks.sign_road_distance_m must be a number between 1 and 100")

    bbox = tuple(file_data.get("area", {}).get("bbox", (19.80, 49.95, 20.15, 50.15)))
    if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        raise ConfigError("area.bbox must be [min_lon, min_lat, max_lon, max_lat] with min < max")

    main_classes = tuple(checks.get("main_road_classes", DEFAULT_MAIN_CLASSES))
    if not main_classes:
        raise ConfigError("checks.main_road_classes must not be empty")

    return RunConfig(
        source_kind=kind,
        source_kind_from=kind_from,
        data_dir=resolved_data,
        output_dir=resolved_out,
        bbox=tuple(float(x) for x in bbox),
        sign_road_distance_m=float(distance),
        main_road_classes=main_classes,
        association_excluded_classes=tuple(checks.get("association_excluded_classes", ("service",))),
        databricks=dict(src.get("databricks", {})),
        config_file=config_file,
    )
