"""Decide what to OFFER a private seller — anchored on the actual conversation.

Your flow: paste a VIN + free-text context (listing + your chat with the seller).
One call figures out where the negotiation currently stands (the seller's latest
price, anything you already offered or agreed) and recommends your single best
NEXT move — aggressive but credible — using the market comps and history findings
as leverage. Then it drafts a send-ready reply.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import llm
from .report import VehicleReport


@dataclass
class NegotiationResult:
    final_offer: int | None = None
    rationale: str | None = None       # one concise reason for the number
    current_state: str | None = None   # where the deal stands, per the chat
    draft_message: str | None = None   # ready-to-send reply to the seller
    available: bool = True
    note: str = ""


def _market_summary(report: VehicleReport) -> str:
    c = report.comps
    parts = [f"vehicle={report.decoded.full_name}"]
    if report.mileage:
        parts.append(f"mileage={report.mileage}")
    if c.count:
        parts.append(
            f"market_comps(asking): low={c.low} median={c.median} high={c.high} "
            f"n={c.count}"
        )
    else:
        parts.append("market_comps=unavailable")
    h = report.history
    parts.append(f"history={h.overall} (title={h.title_status}, auction={h.auction_status})")
    if h.title_brands:
        parts.append(f"title_brands={','.join(h.title_brands)}")
    if h.auction_details:
        parts.append("SALVAGE_AUCTION_RECORD=[" + "; ".join(h.auction_details) + "]")
    if report.recalls and not report.recalls.error:
        parts.append(f"open_recalls={report.recalls.count}")
    return " | ".join(parts)


_SYSTEM = (
    "You are a shrewd, realistic used-car buyer. From the CONVERSATION, first work "
    "out where the negotiation currently stands: the lowest price the seller has "
    "stated or agreed to, any number I've already offered, and anything tentatively "
    "agreed. Then recommend my single best NEXT offer — aggressive but credible — "
    "ANCHORED ON THE CURRENT STATE of the deal: if the seller has already come down, "
    "build from that number, do NOT restart from the original asking price. Use the "
    "market comps and history findings (salvage/title/odometer/needed repairs) as "
    "leverage. Never exceed fair value; never lowball so hard it kills the deal. "
    'Respond with ONLY JSON: {"offer": int, "rationale": "<=2 sentences, why this '
    'number given where the deal already is", "current_state": "one line: where '
    'the negotiation stands now"}.'
)


def negotiate_offer(
    report: VehicleReport, context: str, progress=None
) -> NegotiationResult:
    p = progress or (lambda *_: None)
    result = NegotiationResult()
    if not llm.available():
        result.available = False
        result.note = "LLM unavailable (install boto3 + set AWS creds), or run with --no-llm"
        return result

    p("working out an offer")
    user = (f"MARKET & VEHICLE:\n{_market_summary(report)}\n\n"
            f"CONVERSATION / CONTEXT:\n{context[:4000]}")
    data, _ = llm.chat_json(_SYSTEM, [{"role": "user", "content": user}])
    if not data or "offer" not in data:
        result.note = "model did not return a usable offer"
        return result
    try:
        result.final_offer = int(data["offer"])
    except (TypeError, ValueError):
        result.note = "model returned a non-numeric offer"
        return result
    result.rationale = data.get("rationale") or None
    result.current_state = data.get("current_state") or None

    p("drafting a reply to the seller")
    result.draft_message = _draft_reply(report, context, result.final_offer)
    return result


def _draft_reply(report: VehicleReport, context: str, offer: int) -> str | None:
    """A short, ready-to-send message continuing the negotiation with the seller."""
    system = (
        "Write a short message I will send a PRIVATE car seller to continue our "
        "negotiation (or to open with an offer if there's no prior chat). Pick up "
        "naturally from where the conversation already is — acknowledge any price "
        "they've already come down to, don't contradict what was agreed. Tone: "
        "friendly but firm, like a normal buyer texting — NOT a form letter, and "
        "never mention any tool, report, or AI. Use the concrete facts (salvage/"
        "title, auction record, odometer, needed repairs, comps) as the reason for "
        f"my number, and clearly land on ${offer:,}. 2-5 sentences. "
        'Return ONLY JSON: {"message": str}.'
    )
    user = f"FINDINGS:\n{_market_summary(report)}\n\nCONVERSATION SO FAR:\n{context[:4000]}"
    data, _ = llm.chat_json(system, [{"role": "user", "content": user}])
    return (data or {}).get("message") or None
