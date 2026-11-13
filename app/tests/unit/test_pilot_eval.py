"""Stage 3 sampling and evaluation (ADR-005 §4.1, §5; PRD AC-33, AC-35e, AC-37-AC-39)."""

from __future__ import annotations

import pandas as pd
import pytest

from mapqa.pilot import evaluate as ev
from mapqa.pilot.sample import SampleError, build_sample

from conftest import DATA_DIR

SIGNS = DATA_DIR / "signs"


@pytest.fixture(scope="module")
def real_sample():
    signs = pd.read_csv(SIGNS / "signs.csv", dtype=str)
    labels = pd.read_csv(SIGNS / "labels.csv", dtype=str)
    return signs, labels, *build_sample(signs, labels)


# --- sample (§4.1) -----------------------------------------------------------------------------

def test_sample_has_20_certain_items_one_per_sequence(real_sample):
    signs, labels, items, strata = real_sample
    assert len(items) == 20
    assert len({i["sequence"] for i in items}) == 20
    certain = set(labels.loc[labels.status == "certain", "photo_id"])
    assert all(i["item_id"] in certain for i in items)


def test_sample_strata_sizes(real_sample):
    _, labels, items, strata = real_sample
    counts = pd.Series(strata).value_counts().to_dict()
    assert counts == {"numeric": 12, "no_speed_sign": 3, "end": 2, "other_prohibitory": 2, "zone": 1}
    neg_types = labels.set_index("photo_id").loc[[i["item_id"] for i in items], "negative_type"].value_counts()
    assert neg_types["cancelled_sign"] == 1 and neg_types["false_positive_object"] == 1
    assert neg_types["no_speed_sign"] == 2 and neg_types["zone"] == 1


def test_sample_covers_every_numeric_value(real_sample):
    _, labels, items, strata = real_sample
    values = labels.set_index("photo_id").loc[[i for i, s in strata.items() if s == "numeric"], "sign_value"]
    assert set(values) == {str(v) for v in range(20, 121, 10)}


def test_sample_logo_is_one_the_detector_reads_as_a_speed_limit(real_sample):
    signs, labels, items, _ = real_sample
    lab = labels.set_index("photo_id")
    logo = [i["item_id"] for i in items if lab.loc[i["item_id"], "negative_type"] == "false_positive_object"]
    assert signs.set_index("photo_id").loc[logo[0], "mapillary_value"].startswith("regulatory--maximum-speed-limit-")


def test_sample_items_carry_no_label_data(real_sample):
    _, _, items, _ = real_sample
    assert all(set(i) == {"item_id", "crop", "crop_sha256", "sequence"} for i in items)
    assert all(i["crop"] == f"signs/crops/{i['item_id']}.jpg" for i in items)


def test_sample_is_deterministic(real_sample):
    signs, labels, items, strata = real_sample
    assert build_sample(signs.sample(frac=1, random_state=1), labels.sample(frac=1, random_state=2)) == (items, strata)


def test_unfillable_stratum_is_named(real_sample):
    signs, labels, _, _ = real_sample
    no_zone = labels[labels.negative_type != "zone"]
    with pytest.raises(SampleError, match="zone"):
        build_sample(signs, no_zone)


# --- detector baseline -------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("regulatory--maximum-speed-limit-20--g1", "20"),
    ("regulatory--maximum-speed-limit-100--g1", "100"),
    ("regulatory--end-of-maximum-speed-limit-30--g2", "none"),
    ("regulatory--speed-limit-zone--g1", "none"),
    (None, "none"),
])
def test_detector_answer(value, expected):
    assert ev.detector_answer(value) == expected


# --- metrics (§5) on a hand-built table ---------------------------------------------------------

JEV, LD, LUNA = "typesafe/jev-1.14", "openai/gpt-6-luna-decisions", "openai/gpt-6-luna"
# synthetic: 5 items -- 3 numeric, 2 negatives (one the detector reads as 80); item e is uncertain
LABELS = pd.DataFrame({"photo_id": list("abcde"), "sign_value": ["20", "50", "none", "none", "30"],
                       "status": ["certain"] * 4 + ["uncertain"], "negative_type": [None, None, "zone", "cancelled_sign", None]})
SIGNS_DF = pd.DataFrame({"photo_id": list("abcde"), "mapillary_value": [
    "regulatory--maximum-speed-limit-20--g1", "regulatory--maximum-speed-limit-50--g1",
    "regulatory--speed-limit-zone--g1", "regulatory--maximum-speed-limit-80--g1", "regulatory--maximum-speed-limit-30--g1"]})
STRATA = {"a": "numeric", "b": "numeric", "c": "zone", "d": "other_prohibitory", "e": "numeric"}


def rd(model, item, status, answer=None, raw=None, p=None):
    return {"model": model, "item_id": item, "status": status, "answer": answer,
            "raw_choice": raw if raw is not None else answer, "probability": p}


READINGS = [
    # Jev: a ok, b abstained (raw right), c ok none, d wrong 80
    rd(JEV, "a", "answered", "20", p=1.0), rd(JEV, "b", "abstained", raw="50", p=0.6),
    rd(JEV, "c", "answered", "none", p=1.0), rd(JEV, "d", "answered", "80", p=0.9), rd(JEV, "e", "answered", "30", p=1),
    # Luna Decisions: a ok, b ok, c wrong 30, d model error
    rd(LD, "a", "answered", "20", p=1.0), rd(LD, "b", "answered", "50", p=1.0),
    rd(LD, "c", "answered", "30", p=0.8), rd(LD, "d", "model_unavailable"), rd(LD, "e", "answered", "30", p=1),
    # Luna chat: b ok, others mixed
    rd(LUNA, "a", "uncertain", raw="20"), rd(LUNA, "b", "answered", "50"),
    rd(LUNA, "c", "answered", "none"), rd(LUNA, "d", "answered", "none"), rd(LUNA, "e", "answered", "30"),
]


@pytest.fixture()
def metrics():
    return ev.evaluate(READINGS, LABELS, SIGNS_DF, STRATA)


def test_only_certain_items_counted(metrics):
    assert metrics["certain_items"] == 4 and metrics["excluded_uncertain"] == 1


def test_per_model_metrics_with_denominators(metrics):
    jev = metrics["models"][JEV]
    assert jev["accuracy_before"] == {"correct": 3, "n": 4}  # b's raw choice was right
    assert jev["accuracy_after"] == {"correct": 2, "n": 3}
    assert jev["coverage"] == {"answered": 3, "n": 4}
    assert jev["status_counts"]["abstained"] == 1
    assert jev["negatives"] == {"n": 2, "correct": 1, "wrong_value": 1}
    assert jev["positives"] == {"n": 2, "correct": 1}
    ld = metrics["models"][LD]
    assert ld["accuracy_before"] == {"correct": 2, "n": 3}  # the error is not an output
    assert ld["status_counts"]["model_unavailable"] == 1
    assert ld["negatives"] == {"n": 2, "correct": 0, "wrong_value": 1}


def test_detector_baseline(metrics):
    det = metrics["detector"]
    assert det["accuracy_after"] == {"correct": 3, "n": 4}
    assert det["negatives"] == {"n": 2, "correct": 1, "wrong_value": 1}


def test_tier1_selection_more_correct_then_negatives_then_jev():
    m = ev.evaluate(READINGS, LABELS, SIGNS_DF, STRATA)
    assert m["tier1_model"] == JEV  # 2 vs 2 correct, 1 vs 1 wrong on negatives -> Jev
    better_ld = [r for r in READINGS if not (r["model"] == LD and r["item_id"] == "c")] + [rd(LD, "c", "answered", "none", p=1)]
    assert ev.evaluate(better_ld, LABELS, SIGNS_DF, STRATA)["tier1_model"] == LD


def test_chain_metrics(metrics):
    ch = metrics["chain"]
    assert ch["tier1_model"] == JEV
    assert ch["tiers"]["decision_model"] == {"received": 4, "resolved": 3, "passed_on": 1, "correct": 2}
    assert ch["tiers"]["general_llm"] == {"received": 1, "resolved": 1, "passed_on": 0, "correct": 1}
    assert ch["tiers"]["human"] == {"received": 0}
    assert ch["end_to_end"] == {"correct": 3, "resolved": 4, "n": 4}
    assert ch["tier2_new_calls"] == 0


def test_manual_queue_lists_both_tiers():
    readings = [r for r in READINGS if not (r["model"] == LUNA and r["item_id"] == "b")] + [rd(LUNA, "b", "unreadable", raw="unreadable")]
    m = ev.evaluate(readings, LABELS, SIGNS_DF, STRATA)
    q = m["chain"]["manual_queue"]
    assert q == [{"item_id": "b", "reason": "model_unresolved", "tier1": "abstained (50, p=0.60)",
                  "tier2": "unreadable"}]


def test_missing_reading_is_an_input_error():
    with pytest.raises(ValueError, match="no reading"):
        ev.evaluate([r for r in READINGS if r["item_id"] != "a"], LABELS, SIGNS_DF, STRATA)
