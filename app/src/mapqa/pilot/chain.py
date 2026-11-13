"""Escalation chain (ADR-005 §4.4, PRD AC-35a-AC-35d): a pure function over two readings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    model: str
    item_id: str
    status: str
    answer: str | None = None


@dataclass(frozen=True)
class ChainResult:
    answer: str | None
    tier: str  # decision_model | general_llm | human
    reason: str | None  # None | model_unresolved | budget_exhausted
    used_tier2: bool


def resolve(tier1, tier2) -> ChainResult:
    """`tier1`/`tier2` are any objects with `status` and `answer` (reader.Reading or Step)."""
    if tier1.status == "answered":
        return ChainResult(tier1.answer, "decision_model", None, False)
    if tier1.status != "abstained":
        reason = "budget_exhausted" if tier1.status == "budget_exhausted" else "model_unresolved"
        return ChainResult(None, "human", reason, False)
    if tier2 is not None and tier2.status == "answered":
        return ChainResult(tier2.answer, "general_llm", None, True)
    reason = "budget_exhausted" if tier2 is not None and tier2.status == "budget_exhausted" else "model_unresolved"
    return ChainResult(None, "human", reason, True)
