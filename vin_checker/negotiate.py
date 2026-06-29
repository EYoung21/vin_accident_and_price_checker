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
    deal_agreed: bool = False          # consensus already reached → confirm, don't re-offer
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
    "You are a shrewd, realistic used-car buyer. Read the ENTIRE conversation "
    "between ME (the buyer) and THIS seller and work out the deal state:\n"
    "- asking_price: the seller's current asking price for THIS car.\n"
    "- my_last_offer: the most recent price I (the buyer) proposed. Read the thread IN "
    "ORDER: only what the seller says AFTER my latest price bears on accepting it — a "
    "seller line that appears BEFORE my price cannot be a rejection or counter of it "
    "(e.g. the seller saying the part was 'with the 6000' BEFORE I then ask 'would you "
    "do 5.8?' is not a rejection of 5.8).\n"
    "- seller_accepted: TRUE if the seller agreed to my_last_offer. Judge from CONTEXT "
    "and CHRONOLOGY, not keywords. ACCEPTANCE (set true) = after I name a price, the "
    "seller does ANY of these WITHOUT countering a different number: replies about the "
    "meetup/timing even tersely ('tomorrow?', 'what time?', 'come get it', 'yes sir'), "
    "asks for or gives an address, talks handoff/payment, or sweetens the deal (offers "
    "to install a part, fill the tank, throw extras in). This is acceptance of the "
    "PRICE even if the exact meet time is still being worked out — an agreed price with "
    "the day/hour still TBD is STILL an agreed price; do NOT downgrade it to "
    "'unconfirmed' or 'pending' just because the schedule isn't locked, and do NOT "
    "re-ask for a number the seller already accepted. NOT acceptance when the seller: "
    "counters a DIFFERENT number, defers the DECISION itself ('let me think about it', "
    "'I'll think it over') with no engagement on the sale, hedges on the PRICE "
    "(\"can't go that low\"), or is merely scheduling a VIEWING before any number "
    "exists ('can I see it tomorrow?').\n"
    "- DO NOT mistake a SCHEDULE deferral for a price deferral. If the seller named no "
    "counter to my price and is giving their address and/or piling on freebies (free "
    "install, fill the tank, etc.), then 'I'm off at 5', 'I'll let you know this week', "
    "'might be today' is ONLY about WHEN to meet — the PRICE is accepted; set "
    "seller_accepted=true and deal_agreed=true at my price, with the meet time pending. "
    "A seller motivated to offload the car (e.g. 'just tryna get it gone', 'moving "
    "states') who keeps adding value has accepted, not stalled.\n"
    "Then recommend my best NEXT move:\n"
    "- If seller_accepted is true: the deal is DONE at my_last_offer — recommend "
    "EXACTLY that number. Never negotiate against yourself or raise it.\n"
    "- NEVER recommend more than asking_price. NEVER raise my own previous offer "
    "unless the seller explicitly rejected it AND countered with a higher number.\n"
    "- If no price has been discussed yet: open below asking, using comps/history "
    "(salvage/title/odometer/repairs) as leverage.\n"
    "ALSO detect CONSENSUS: if a final price is already mutually agreed — both sides "
    "aligned on a number, or you're now just arranging the handoff (pickup/payment/"
    "time) — set deal_agreed=true and agreed_price to that number, and do NOT propose "
    "a new or different number.\n"
    "Ignore unrelated marketplace listings, menus, and other cars in the paste.\n"
    'Respond with ONLY JSON: {"offer": int, "asking_price": int|null, '
    '"my_last_offer": int|null, "seller_accepted": true|false, "deal_agreed": '
    'true|false, "agreed_price": int|null, "rationale": "<=2 sentences", '
    '"current_state": "one line: where the deal stands now"}.'
)


def _int(v):
    import re
    d = re.sub(r"[^0-9]", "", str(v or ""))
    return int(d) if d else None

# Pasted Marketplace threads can be long; keep enough to include the whole chat.
_CTX_LIMIT = 16000


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
            f"CONVERSATION / CONTEXT:\n{context[:_CTX_LIMIT]}")
    data, _ = llm.chat_json(_SYSTEM, [{"role": "user", "content": user}])
    if not data or (offer := _int(data.get("offer"))) is None:
        result.note = "model did not return a usable offer"
        return result

    # Deterministic guardrails on top of the model's number:
    asking = _int(data.get("asking_price"))
    my_last = _int(data.get("my_last_offer"))
    accepted = bool(data.get("seller_accepted"))
    agreed = bool(data.get("deal_agreed")) or accepted

    if agreed:
        # Consensus reached — lock the agreed number, don't re-offer.
        offer = _int(data.get("agreed_price")) or my_last or offer
    elif asking and offer > asking:
        offer = asking         # never offer above the seller's asking price

    result.final_offer = offer
    result.deal_agreed = agreed
    result.rationale = data.get("rationale") or None
    result.current_state = data.get("current_state") or None

    p("confirming the deal" if agreed else "drafting a reply to the seller")
    result.draft_message = _draft_reply(report, context, result.final_offer, agreed)
    return result


def _draft_reply(report: VehicleReport, context: str, offer: int,
                 deal_agreed: bool = False) -> str | None:
    """A short, ready-to-send message. If the deal's already agreed, confirm and move
    to logistics; otherwise continue the negotiation."""
    if deal_agreed:
        system = (
            f"The price is ALREADY AGREED at ${offer:,}. Do NOT re-offer, re-negotiate, "
            "or restate justifications. Write a short, friendly text that confirms I'll "
            "take it at that price and moves to logistics — reply naturally to their "
            "last message and ask the practical next step (when/where to meet, payment, "
            "pickup). Sound like a real person; never mention any tool/AI. 1-3 sentences. "
            'Return ONLY JSON: {"message": str}.'
        )
        user = f"CONVERSATION SO FAR:\n{context[:_CTX_LIMIT]}"
        data, _ = llm.chat_json(system, [{"role": "user", "content": user}])
        return (data or {}).get("message") or None

    system = (
        "Write my next text to a PRIVATE car seller — it must read as a natural REPLY "
        "to their MOST RECENT message. First acknowledge what they just said or did "
        "(e.g., sent the VIN, said it's well maintained, offered a deal, marked it "
        "available), then continue. Match their casual texting tone; sound like a real "
        "person, NOT a form letter, and never mention any tool/report/AI. "
        "If a price was already agreed, confirm it. If they already came down, build "
        "from that. If NO price has been discussed yet, don't lowball out of nowhere — "
        "show genuine interest and raise the number gently, e.g. 'would you do $X?', "
        "using any noted issues (repairs, mileage, history) as soft justification. "
        f"Land on ${offer:,}. Keep it 2-4 sentences. "
        'Return ONLY JSON: {"message": str}.'
    )
    user = f"FINDINGS:\n{_market_summary(report)}\n\nCONVERSATION SO FAR:\n{context[:_CTX_LIMIT]}"
    data, _ = llm.chat_json(system, [{"role": "user", "content": user}])
    return (data or {}).get("message") or None
