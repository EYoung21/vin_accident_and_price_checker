"""Rank multiple candidate cars to decide which to go see.

Deterministic score first (price-vs-market + history verdict), with an optional
LLM pass that folds in proximity, seller red flags, and listing notes into a
human-style recommendation. Falls back cleanly to the deterministic score.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import llm
from .report import VehicleReport


@dataclass
class Candidate:
    report: VehicleReport
    asking_price: int | None = None
    distance_miles: float | None = None
    listing_notes: str = ""


def _deal_delta(c: Candidate) -> int | None:
    """How far asking is below market median (positive = good deal)."""
    med = c.report.comps.median
    if med and c.asking_price:
        return med - c.asking_price
    return None


def _score(c: Candidate) -> float:
    score = 0.0
    delta = _deal_delta(c)
    if delta is not None:
        score += delta / 1000.0  # +1 per $1k under market
    h = c.report.history.overall
    score += {"clean": 5, "inconclusive": 0, "flagged": -50}.get(h, 0)
    if c.distance_miles is not None:
        score -= c.distance_miles / 100.0  # mild distance penalty
    score -= c.report.recalls.count * 0.5
    return score


def rank(candidates: list[Candidate], use_llm: bool = True) -> list[tuple[Candidate, float, str]]:
    scored = sorted(candidates, key=_score, reverse=True)
    results = [(c, _score(c), "") for c in scored]
    if not use_llm or len(candidates) < 2:
        return results

    lines = []
    for i, c in enumerate(scored):
        d = c.report.decoded
        delta = _deal_delta(c)
        lines.append(
            f"{i}. {d.full_name} | ask {c.asking_price} | market_median "
            f"{c.report.comps.median} | delta {delta} | history "
            f"{c.report.history.overall} | recalls {c.report.recalls.count} | "
            f"distance_mi {c.distance_miles} | notes: {c.listing_notes[:200]}"
        )
    system = (
        "You are a savvy used-car buyer. Rank these candidates best-to-worst for "
        "going to see in person. Weigh value-vs-market, clean history, proximity, "
        "and red flags. Return ONLY JSON: {\"ranking\":[{\"index\":int,"
        "\"reason\":str}]}."
    )
    data = llm.complete_json(system, "CANDIDATES:\n" + "\n".join(lines))
    if not data or "ranking" not in data:
        return results

    reasons = {r["index"]: r.get("reason", "") for r in data["ranking"]}
    order = [r["index"] for r in data["ranking"] if r["index"] < len(scored)]
    if not order:
        return results
    return [(scored[i], _score(scored[i]), reasons.get(i, "")) for i in order]
