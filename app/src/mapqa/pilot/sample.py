"""Stratified pilot sample (ADR-005 §4.1, PRD AC-33, AC-39). Evaluation side: reads labels to stratify,
but the manifest it produces carries no label data, so the reading step never sees them (AC-37)."""

from __future__ import annotations

import hashlib

import pandas as pd

SALT = "mapqa-pilot-v1:"
NUMERIC_VALUES = [str(v) for v in range(20, 121, 10)]
PROHIBITORY = ("no_entry", "weight", "no_overtaking", "no_stopping")
SPEED_CLASS = "regulatory--maximum-speed-limit-"


class SampleError(Exception):
    """A stratum cannot be filled; nothing is substituted silently."""


def _rank(photo_id: str) -> str:
    return hashlib.sha256((SALT + photo_id).encode()).hexdigest()


def build_sample(signs: pd.DataFrame, labels: pd.DataFrame) -> tuple[list[dict], dict[str, str]]:
    """Return (manifest items without labels, {item_id: stratum}) in a deterministic order."""
    df = labels[labels["status"] == "certain"].merge(
        signs[["photo_id", "sequence", "mapillary_value", "crop_sha256"]], on="photo_id", how="inner")
    df = df.assign(_rank=df["photo_id"].map(_rank)).sort_values(["_rank", "photo_id"]).reset_index(drop=True)
    value = df["sign_value"].fillna("")
    neg_type = df["negative_type"].fillna("")
    detector = df["mapillary_value"].fillna("")
    negative = value == "none"

    plan = [
        ("zone", "zone", negative & (neg_type == "zone"), 1),
        ("end", "end (zone_end/limit_end)", negative & neg_type.isin(["zone_end", "limit_end"]), 2),
        ("other_prohibitory", "other_prohibitory (" + "/".join(PROHIBITORY) + ")",
         negative & neg_type.isin(PROHIBITORY), 1),
        ("other_prohibitory", "other_prohibitory (cancelled_sign)", negative & (neg_type == "cancelled_sign"), 1),
        ("no_speed_sign", "no_speed_sign", negative & (neg_type == "no_speed_sign"), 2),
        ("no_speed_sign", "no_speed_sign (false_positive_object read as a speed limit)",
         negative & (neg_type == "false_positive_object") & detector.str.startswith(SPEED_CLASS), 1),
        *[("numeric", f"numeric {v}", value == v, 1) for v in NUMERIC_VALUES],
        ("numeric", "numeric (extra)", value.str.fullmatch(r"\d+"), 1),
    ]

    items: list[dict] = []
    strata: dict[str, str] = {}
    used_sequences: set[str] = set()
    for stratum, name, mask, n in plan:
        taken = 0
        for row in df[mask].itertuples(index=False):
            if taken == n:
                break
            if row.photo_id in strata or row.sequence in used_sequences:
                continue
            items.append({"item_id": row.photo_id, "crop": f"signs/crops/{row.photo_id}.jpg",
                          "crop_sha256": row.crop_sha256, "sequence": row.sequence})
            strata[row.photo_id] = stratum
            used_sequences.add(row.sequence)
            taken += 1
        if taken < n:
            raise SampleError(f"stratum {name}: needs {n} certain crop(s) from unused sequences, found {taken}")
    return items, strata
