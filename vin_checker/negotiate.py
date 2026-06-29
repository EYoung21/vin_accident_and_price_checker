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
    "You are a shrewd, realistic used-car buyer. Read the ENTIRE conversation "
    "between ME (the buyer) and THIS seller and work out the deal state:\n"
    "- asking_price: the seller's current asking price for THIS car.\n"
    "- my_last_offer: the most recent price I (the buyer) proposed, if any.\n"
    "- seller_accepted: TRUE if the seller agreed to my_last_offer. CRITICAL: a "
    "seller AGREES when, after I name a price, they respond about logistics/timing/"
    "pickup/address (e.g. 'tomorrow?', 'come get it', 'what time?', 'yes sir', "
    "'I'm here', or they share their address) WITHOUT countering a different number. "
    "Coordinating a meetup after a price = ACCEPTANCE, not rejection.\n"
    "Then recommend my best NEXT move:\n"
    "- If seller_accepted is true: the deal is DONE at my_last_offer — recommend "
    "EXACTLY that number. Never negotiate against yourself or raise it.\n"
    "- NEVER recommend more than asking_price. NEVER raise my own previous offer "
    "unless the seller explicitly rejected it AND countered with a higher number.\n"
    "- If no price has been discussed yet: open below asking, using comps/history "
    "(salvage/title/odometer/repairs) as leverage.\n"
    "Ignore unrelated marketplace listings, menus, and other cars in the paste.\n"
    'Respond with ONLY JSON: {"offer": int, "asking_price": int|null, '
    '"my_last_offer": int|null, "seller_accepted": true|false, "rationale": '
    '"<=2 sentences", "current_state": "one line: where the deal stands now"}.'
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
    if accepted and my_last:
        offer = my_last        # deal is done at the price they accepted
    if asking and offer > asking:
        offer = asking         # never offer above the seller's asking price

    result.final_offer = offer
    result.rationale = data.get("rationale") or None
    result.current_state = data.get("current_state") or None

    p("drafting a reply to the seller")
    result.draft_message = _draft_reply(report, context, result.final_offer)
    return result


def _draft_reply(report: VehicleReport, context: str, offer: int) -> str | None:
    """A short, ready-to-send message continuing the negotiation with the seller."""
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
