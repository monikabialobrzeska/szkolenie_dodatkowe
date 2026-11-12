"""Speed-limit interpretation (ADR-002 §3). Pure functions, no I/O."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from mapqa.models import SpeedLimit

# ADR-002 §3.2 — interpretation value and allowed values for the tag_inconsistency check.
IMPLICIT_VALUES: dict[str, int | None] = {
    "PL:urban": 50,
    "PL:rural": 90,
    "PL:expressway": None,  # depends on carriageway type -> unparseable as a value
    "PL:motorway": 140,
    "PL:living_street": 20,
}
ALLOWED_VALUES: dict[str, tuple[int, ...]] = {
    "PL:urban": (50,),
    "PL:rural": (90, 100),
    "PL:expressway": (100, 120),
    "PL:motorway": (140,),
    "PL:living_street": (20,),
}
_ZONE = re.compile(r"PL:zone(\d{1,3})")
_INT = re.compile(r"\d{1,3}")
_MPH = re.compile(r"(\d{1,3}) ?mph")

# Column name (as produced by the source adapter) -> OSM tag key, in output order.
RAW_KEYS = {
    "maxspeed": "maxspeed",
    "maxspeed_forward": "maxspeed:forward",
    "maxspeed_backward": "maxspeed:backward",
    "maxspeed_conditional": "maxspeed:conditional",
    "maxspeed_variable": "maxspeed:variable",
    "source_maxspeed": "source:maxspeed",
    "maxspeed_type": "maxspeed:type",
}


@dataclass(frozen=True)
class ValueResult:
    kmh: int | None
    status: str  # absent | ok | unparseable
    implicit: bool = False
    converted: bool = False


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return str(value)


def category_values(category: str | None) -> tuple[int, ...] | None:
    """Allowed km/h values of an implicit category, or None if it is not a category."""
    if category is None:
        return None
    if category in ALLOWED_VALUES:
        return ALLOWED_VALUES[category]
    zone = _ZONE.fullmatch(category)
    if zone:
        return (int(zone.group(1)),)
    return None


def parse_value(raw: Any) -> ValueResult:
    raw = _clean(raw)
    if raw is None:
        return ValueResult(None, "absent")
    text = raw.strip()
    if _INT.fullmatch(text):
        number = int(text)
        return ValueResult(number, "ok") if 1 <= number <= 200 else ValueResult(None, "unparseable")
    mph = _MPH.fullmatch(text)
    if mph:
        kmh = math.floor(int(mph.group(1)) * 1.609344 + 0.5)
        return ValueResult(kmh, "ok", converted=True) if 1 <= kmh <= 200 else ValueResult(None, "unparseable")
    if text in IMPLICIT_VALUES:
        value = IMPLICIT_VALUES[text]
        return ValueResult(value, "ok", implicit=True) if value is not None else ValueResult(None, "unparseable")
    zone = _ZONE.fullmatch(text)
    if zone:
        return ValueResult(int(zone.group(1)), "ok", implicit=True)
    return ValueResult(None, "unparseable")


def interpret_road(row: Mapping[str, Any]) -> SpeedLimit:
    raw = {tag: _clean(row.get(column)) for column, tag in RAW_KEYS.items()}
    variable = raw["maxspeed:variable"] is not None and raw["maxspeed:variable"] != "no"
    conditional = raw["maxspeed:conditional"] is not None
    flags = {"conditional": conditional, "variable": variable, "raw": raw}

    plain = parse_value(raw["maxspeed"])
    if raw["maxspeed:forward"] is not None or raw["maxspeed:backward"] is not None:
        fwd = parse_value(raw["maxspeed:forward"]) if raw["maxspeed:forward"] is not None else plain
        bwd = parse_value(raw["maxspeed:backward"]) if raw["maxspeed:backward"] is not None else plain
        if "unparseable" in (fwd.status, bwd.status, plain.status):
            return SpeedLimit(kind="unparseable", **flags)
        return SpeedLimit(
            kind="directional",
            value_kmh=plain.kmh,
            forward_kmh=fwd.kmh,
            backward_kmh=bwd.kmh,
            implicit=fwd.implicit or bwd.implicit,
            converted_from_mph=fwd.converted or bwd.converted,
            **flags,
        )
    if plain.status == "ok":
        return SpeedLimit(kind="numeric", value_kmh=plain.kmh, implicit=plain.implicit,
                          converted_from_mph=plain.converted, **flags)
    if plain.status == "unparseable":
        return SpeedLimit(kind="unparseable", **flags)
    return SpeedLimit(kind="missing", **flags)
