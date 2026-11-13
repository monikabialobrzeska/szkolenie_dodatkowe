"""Evaluation step (ADR-005 §5; PRD AC-35e, AC-37, AC-38). The only pilot step that reads reference labels."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pandas as pd

from mapqa.pilot import chain
from mapqa.pilot.reader import CHAT_MODEL, DECISION_MODELS, MODELS, THRESHOLD, VALUES

STATUSES = ("answered", "abstained", "unreadable", "uncertain", "invalid_response", "model_unavailable",
            "not_attempted", "budget_exhausted")
VALID_OUTPUT = {"answered", "abstained", "unreadable", "uncertain"}
PROB_BINS = [("< 0.5", 0.0, 0.5), ("0.5-0.7", 0.5, 0.7), ("0.7-0.9", 0.7, 0.9), ("0.9-<1", 0.9, 1.0), ("1.0", 1.0, 1.01)]
_SPEED = re.compile(r"^regulatory--maximum-speed-limit-(\d+)--")


def detector_answer(mapillary_value) -> str:
    """Mapillary class -> the same answer space as the models; anything but a max-speed sign is `none`."""
    m = _SPEED.match(mapillary_value) if isinstance(mapillary_value, str) else None
    return m.group(1) if m else "none"


def _metrics(rows: list[dict], label: dict[str, str]) -> dict:
    counts = {s: 0 for s in STATUSES}
    before = after = answered = 0
    valid = 0
    neg = {"n": 0, "correct": 0, "wrong_value": 0}
    pos = {"n": 0, "correct": 0}
    for r in rows:
        truth = label[r["item_id"]]
        counts[r["status"]] += 1
        ok = r["status"] == "answered"
        if r["status"] in VALID_OUTPUT:
            valid += 1
            before += r.get("raw_choice") == truth
        if ok:
            answered += 1
            after += r["answer"] == truth
        if truth == "none":
            neg["n"] += 1
            neg["correct"] += ok and r["answer"] == "none"
            neg["wrong_value"] += ok and r["answer"] in VALUES
        elif truth in VALUES:
            pos["n"] += 1
            pos["correct"] += ok and r["answer"] == truth
    return {"accuracy_before": {"correct": before, "n": valid}, "accuracy_after": {"correct": after, "n": answered},
            "coverage": {"answered": answered, "n": len(rows)}, "status_counts": counts,
            "negatives": neg, "positives": pos}


def _prob_bins(rows: list[dict]) -> dict:
    bins = {name: 0 for name, _, _ in PROB_BINS}
    for r in rows:
        p = r.get("probability")
        if p is None:
            continue
        for name, lo, hi in PROB_BINS:
            if lo <= p < hi:
                bins[name] += 1
    return bins


def _describe(r: dict | None) -> str:
    if r is None:
        return "-"
    text = r["status"]
    raw, p = r.get("raw_choice"), r.get("probability")
    if raw and raw != r["status"]:
        text += f" ({raw}" + (f", p={p:.2f}" if p is not None else "") + ")"
    return text


def select_tier1(models: dict) -> str:
    """More correct answered items, then fewer wrong values on negatives, then Jev (ADR-005 §5)."""
    order = {m: i for i, m in enumerate(DECISION_MODELS)}
    return min(DECISION_MODELS, key=lambda m: (-models[m]["accuracy_after"]["correct"],
                                               models[m]["negatives"]["wrong_value"], order[m]))


def evaluate(readings: list[dict], labels: pd.DataFrame, signs: pd.DataFrame, strata: dict[str, str]) -> dict:
    lab = labels.set_index("photo_id")
    item_ids = list(strata)
    certain = [i for i in item_ids if lab.loc[i, "status"] == "certain"]
    label = {i: lab.loc[i, "sign_value"] for i in certain}
    by = {(r["model"], r["item_id"]): r for r in readings}
    for m in MODELS:
        for i in item_ids:
            if (m, i) not in by:
                raise ValueError(f"no reading for {m} on {i}; run `mapqa pilot run` first")

    models = {}
    for m in MODELS:
        rows = [by[(m, i)] for i in certain]
        models[m] = _metrics(rows, label)
        if m in DECISION_MODELS:
            models[m]["probability_bins"] = _prob_bins(rows)

    det_value = signs.set_index("photo_id")["mapillary_value"]
    det_rows = [{"item_id": i, "status": "answered", "answer": detector_answer(det_value.get(i)),
                 "raw_choice": detector_answer(det_value.get(i))} for i in certain]
    detector = _metrics(det_rows, label)

    tier1 = select_tier1(models)
    tiers = {"decision_model": {"received": 0, "resolved": 0, "passed_on": 0, "correct": 0},
             "general_llm": {"received": 0, "resolved": 0, "passed_on": 0, "correct": 0},
             "human": {"received": 0}}
    queue, correct, resolved = [], 0, 0
    final = {}
    for i in certain:
        t1, t2 = by[(tier1, i)], by[(CHAT_MODEL, i)]
        out = chain.resolve(SimpleNamespace(**t1), SimpleNamespace(**t2))
        final[i] = out
        tiers["decision_model"]["received"] += 1
        if out.used_tier2:
            tiers["decision_model"]["passed_on"] += 1
            tiers["general_llm"]["received"] += 1
        if out.tier == "human":
            tiers["human"]["received"] += 1
            if out.used_tier2:
                tiers["general_llm"]["passed_on"] += 1
            elif t1["status"] != "answered":
                tiers["decision_model"]["passed_on"] += 1
            queue.append({"item_id": i, "reason": out.reason, "tier1": _describe(t1),
                          "tier2": _describe(t2) if out.used_tier2 else "-"})
        else:
            tiers[out.tier]["resolved"] += 1
            resolved += 1
            hit = out.answer == label[i]
            tiers[out.tier]["correct"] += hit
            correct += hit

    per_item = []
    for i in item_ids:
        row = {"item_id": i, "stratum": strata[i], "label": lab.loc[i, "sign_value"], "status": lab.loc[i, "status"],
               "detector": detector_answer(det_value.get(i))}
        row.update({m: _describe(by[(m, i)]) for m in MODELS})
        if i in final:
            row["chain"] = f"{final[i].answer or '-'} [{final[i].tier}]"
        per_item.append(row)

    return {"threshold": THRESHOLD, "items": len(item_ids), "certain_items": len(certain),
            "excluded_uncertain": len(item_ids) - len(certain), "models": models, "detector": detector,
            "tier1_model": tier1,
            "chain": {"tier1_model": tier1, "tier2_model": CHAT_MODEL, "tiers": tiers,
                      "end_to_end": {"correct": correct, "resolved": resolved, "n": len(certain)},
                      "tier2_reused": tiers["general_llm"]["received"], "tier2_new_calls": 0,
                      "manual_queue": queue},
            "per_item": per_item}
