"""Pros/cons for the buy decision.

Uses the full picture (decode, value, history, recalls, safety) + the seller
conversation to list concrete reasons for and against buying THIS car. Falls back
to a deterministic list from the structured data when the LLM is off.
"""

from __future__ import annotations

from . import llm
from .report import VehicleReport


def _summary(r: VehicleReport) -> str:
    parts = [r.decoded.full_name or r.decoded.vin]
    if r.mileage:
        parts.append(f"{r.mileage:,} mi")
    c = r.comps
    if c.count and c.median:
        parts.append(f"market median ${c.median:,} (range ${c.low:,}-${c.high:,}, {c.count} comps)")
    h = r.history
    parts.append(f"title={h.title_status}, auction={h.auction_status}"
                 + (f", brands={','.join(h.title_brands)}" if h.title_brands else ""))
    if h.auction_details:
        parts.append("auction record: " + "; ".join(h.auction_details))
    rc = r.recalls
    if not rc.error:
        parts.append(f"{rc.count} open recalls, {rc.complaint_count or 0} complaints")
    if r.safety and r.safety.overall:
        parts.append(f"NCAP {r.safety.overall}/5")
    return " | ".join(parts)


def _deterministic(r: VehicleReport) -> tuple[list[str], list[str]]:
    h, rc, pros, cons = r.history, r.recalls, [], []
    if h.title_status == "clean":
        pros.append("Clean title — no brand or accident on record")
    if h.auction_status == "clean":
        pros.append("No salvage-auction record found")
    if not rc.error and rc.count == 0:
        pros.append("No open safety recalls")
    if r.safety and str(r.safety.overall or "").isdigit() and int(r.safety.overall) >= 5:
        pros.append("Top NCAP crash-test rating (5/5)")

    if h.title_brands:
        cons.append(", ".join(h.title_brands) + " title — insurance/financing/resale hit")
    if h.auction_status == "flagged":
        cons.append("Salvage-auction history (was wrecked)")
    if r.mileage and str(r.decoded.year or "").isdigit():
        age = max(1, 2026 - int(r.decoded.year))
        if r.mileage / age > 15000:
            cons.append(f"High mileage ({r.mileage:,} mi) for a {r.decoded.year}")
    if not rc.error and rc.count:
        cons.append(f"{rc.count} open recall(s) to get done")
    if not rc.error and (rc.complaint_count or 0) >= 100:
        cons.append(f"{rc.complaint_count} owner complaints for this model")
    return pros, cons


def pros_cons(report: VehicleReport, context: str = "", use_llm: bool = True,
              progress=None) -> tuple[list[str], list[str]]:
    if not (use_llm and llm.available()):
        return _deterministic(report)
    (progress or (lambda *_: None))("weighing pros and cons")
    system = (
        "You assess a used car for a buyer. From the DATA and the seller "
        "CONVERSATION, list the concrete PROS and CONS of buying THIS specific car. "
        "Be specific and factual: mileage, title/history, service already done, "
        "repairs it needs, recalls, price vs market, and seller signals. Credit any "
        "specific work the seller stated (e.g. new tires/brakes, fuel pump, service) "
        "as a PRO — do NOT claim 'no service history' if they listed work done. "
        "Short phrases, 2-5 each. Ignore unrelated marketplace listings/menus. "
        'Return ONLY JSON: {"pros": [str], "cons": [str]}.'
    )
    user = f"DATA:\n{_summary(report)}\n\nCONVERSATION/CONTEXT:\n{context[:16000]}"
    data, _ = llm.chat_json(system, [{"role": "user", "content": user}])
    if not data:
        return _deterministic(report)
    pros = [str(x) for x in (data.get("pros") or [])][:5]
    cons = [str(x) for x in (data.get("cons") or [])][:5]
    det = _deterministic(report)
    return (pros or det[0], cons or det[1])
