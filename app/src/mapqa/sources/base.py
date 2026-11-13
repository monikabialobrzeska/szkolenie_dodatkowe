"""Source adapter interface and InputBundle (ADR-001 §4.1-§4.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import geopandas as gpd
import pandas as pd

from mapqa.config import RunConfig


class InputError(Exception):
    """A required input is missing, unreadable or incomplete (exit code 3)."""


class SourceUnavailable(Exception):
    """The selected source cannot be used (exit code 4)."""


class SourceNotAvailable(SourceUnavailable):
    """The selected source is not implemented in this version (exit code 4)."""


@dataclass(frozen=True)
class InputStatus:
    name: str
    location: str
    ok: bool
    problem: str | None = None


@dataclass
class InputBundle:
    roads: gpd.GeoDataFrame
    signs: gpd.GeoDataFrame
    photos: pd.DataFrame
    photo_links: pd.DataFrame
    overture: pd.DataFrame
    row_counts: dict[str, int]
    snapshot: dict[str, Any]
    read_at: str
    skipped: dict[str, int] = field(default_factory=dict)


class SourceAdapter(Protocol):
    kind: str

    def location(self) -> str: ...
    def input_locations(self) -> dict[str, str]: ...
    def check(self) -> list[InputStatus]: ...
    def checksums(self) -> dict[str, str]: ...
    def load(self) -> InputBundle: ...


def adapter_for(config: RunConfig) -> SourceAdapter:
    if config.source_kind == "files":
        from mapqa.sources.files import FilesSource

        return FilesSource(config)
    if config.source_kind == "databricks":
        from mapqa.sources.databricks import DatabricksSource

        return DatabricksSource(config)
    raise SourceNotAvailable(f"source {config.source_kind}: not available yet in this version")
