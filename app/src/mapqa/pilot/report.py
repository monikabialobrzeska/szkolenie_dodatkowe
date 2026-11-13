"""Pilot report (ADR-005 §5): every metric with its denominator; labels shown as a working reference."""

from __future__ import annotations

from mapqa.pilot.reader import BUDGET_PER_MODEL, MODELS

SHORT = {"typesafe/jev-1.14": "Jev", "openai/gpt-6-luna-decisions": "Luna Decisions", "openai/gpt-6-luna": "Luna"}


def frac(correct: int, n: int) -> str:
    c = correct
    return f"{c} / {n}" + (f" ({c / n:.0%})" if n else "")


def _row(name: str, cells: list[str]) -> str:
    return f"| {name} | " + " | ".join(cells) + " |"


def render(metrics: dict, calls: dict) -> str:
    cols = [*MODELS, "detector"]
    m = {**metrics["models"], "detector": metrics["detector"]}
    head = "| Metric | " + " | ".join(SHORT.get(c, "Mapillary detector") for c in cols) + " |"
    sep = "|---|" + "---|" * len(cols)
    out = [
        "# Model reading pilot — evaluation report",
        "",
        "Offline evaluation of sign-crop reading (PRD AC-29–AC-39, ADR-005). Not used in the review queue.",
        "Reference labels are a **working reference** (two independent readings), not ground truth.",
        f"Only items with a `certain` label are counted: {metrics['certain_items']} of {metrics['items']} "
        f"({metrics['excluded_uncertain']} excluded).",
        f"Abstention threshold: **{metrics['threshold']}** on the decision model's highest probability, "
        "fixed before the run and not tuned on this sample.",
        "",
        "## Part A — side by side",
        "",
        head, sep,
        _row("Accuracy before abstention", [frac(**m[c]["accuracy_before"]) for c in cols]),
        _row("Accuracy after abstention", [frac(**m[c]["accuracy_after"]) for c in cols]),
        _row("Coverage (answered)", [frac(m[c]["coverage"]["answered"], m[c]["coverage"]["n"]) for c in cols]),
        _row("Numeric signs read correctly", [frac(m[c]["positives"]["correct"], m[c]["positives"]["n"]) for c in cols]),
        _row("Negatives answered `none`", [frac(m[c]["negatives"]["correct"], m[c]["negatives"]["n"]) for c in cols]),
        _row("Negatives given a speed value", [frac(m[c]["negatives"]["wrong_value"], m[c]["negatives"]["n"]) for c in cols]),
    ]
    for status in ("abstained", "unreadable", "uncertain", "invalid_response", "model_unavailable", "not_attempted",
                   "budget_exhausted"):
        out.append(_row(f"`{status}`", [str(m[c]["status_counts"][status]) for c in cols]))
    out += ["",
            "Accuracy before abstention counts the model's raw choice over items with a valid output; after abstention "
            "only accepted answers. Errors are never counted as answers. The Mapillary detector baseline maps a "
            "`regulatory--maximum-speed-limit-N` class to N and anything else to `none`; the sample was found through "
            "the detector, so its result on numeric signs is favourable by construction.",
            "",
            "Probability of the chosen option (decision models):",
            "",
            "| Probability | " + " | ".join(SHORT[x] for x in metrics["models"] if "probability_bins" in metrics["models"][x]) + " |",
            "|---|---|---|"]
    bins = [metrics["models"][x]["probability_bins"] for x in metrics["models"] if "probability_bins" in metrics["models"][x]]
    for name in bins[0]:
        out.append(f"| {name} | " + " | ".join(str(b[name]) for b in bins) + " |")

    ch = metrics["chain"]
    t = ch["tiers"]
    e2e = ch["end_to_end"]
    out += ["",
            "## Part B — escalation chain",
            "",
            f"Tier 1: **{SHORT[ch['tier1_model']]}** (`{ch['tier1_model']}`), selected by the rule fixed in ADR-005 §5: more "
            "correct accepted answers, then fewer speed values on negatives, then Jev. Tier 2: "
            f"**{SHORT[ch['tier2_model']]}** with medium reasoning.",
            f"Tier-2 readings were reused from Part A (identical request, cache): {ch['tier2_reused']} reused, "
            f"{ch['tier2_new_calls']} new calls.",
            "",
            "| Tier | Received | Resolved | Passed on | Correct (of resolved) |",
            "|---|---|---|---|---|",
            f"| 1 decision model | {t['decision_model']['received']} | {t['decision_model']['resolved']} | "
            f"{t['decision_model']['passed_on']} | {frac(t['decision_model']['correct'], t['decision_model']['resolved'])} |",
            f"| 2 general LLM | {t['general_llm']['received']} | {t['general_llm']['resolved']} | "
            f"{t['general_llm']['passed_on']} | {frac(t['general_llm']['correct'], t['general_llm']['resolved'])} |",
            f"| 3 human (manual queue) | {t['human']['received']} | - | - | - |",
            "",
            f"End to end: accuracy {frac(e2e['correct'], e2e['resolved'])} of automatically resolved items; "
            f"coverage {frac(e2e['resolved'], e2e['n'])}; reached the manual queue {frac(t['human']['received'], e2e['n'])}.",
            "",
            "The chain is **optimistic**: tier 1 was chosen on the same items it is evaluated on.",
            "",
            "### Manual queue",
            ""]
    if ch["manual_queue"]:
        out += ["| Item | Reason | Tier 1 | Tier 2 |", "|---|---|---|---|"]
        out += [f"| {q['item_id']} | `{q['reason']}` | {q['tier1']} | {q['tier2']} |" for q in ch["manual_queue"]]
    else:
        out.append("No items reached the manual queue.")

    out += ["", "## Calls", "",
            f"Budget: {BUDGET_PER_MODEL} calls per model per run (AC-31). Paid calls recorded in `calls.jsonl` (all runs):",
            "", "| Model | Paid calls | Cache hits | Upstream cost (USD) |", "|---|---|---|---|"]
    for model in MODELS:
        c = calls.get(model, {"paid": 0, "cached": 0, "cost": 0.0})
        out.append(f"| {SHORT[model]} | {c['paid']} | {c['cached']} | {c['cost']:.6f} |")

    out += ["", "## Per item", "",
            "| Item | Stratum | Label (working ref.) | Detector | " + " | ".join(SHORT[x] for x in MODELS) + " | Chain |",
            "|---|---|---|---|" + "---|" * len(MODELS) + "---|"]
    for r in metrics["per_item"]:
        label = r["label"] + ("" if r["status"] == "certain" else " (uncertain, not counted)")
        out.append(f"| {r['item_id']} | {r['stratum']} | {label} | {r['detector']} | "
                   + " | ".join(r[x] for x in MODELS) + f" | {r.get('chain', '-')} |")
    return "\n".join(out) + "\n"
