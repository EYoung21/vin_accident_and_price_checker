"""Decide what to OFFER a private seller, then push the number down.

Your flow: paste a VIN + free-text context (listing description, your chat with
the seller). The model proposes an offer grounded in the real market comps and
the context, then we re-prompt it ("can you go lower?") in a loop. Each round it
either lowers the number or holds; when it holds (or stops dropping), that's the
price you take to the seller. Capped at a few rounds so we don't burn LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm
from .report import VehicleReport


@dataclass
class NegotiationResult:
    final_offer: int | None = None
    rounds: list[dict] = field(default_factory=list)  # [{offer, rationale, held}]
    draft_message: str | None = None  # ready-to-send reply to the seller
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
    "You are a shrewd but realistic used-car buyer helping me decide what price to "
    "OFFER a private-party seller. Offers should be aggressive-but-credible: below "
    "the asking-price market median (asking prices run above sold prices), adjusted "
    "for mileage, history flags, needed repairs, and anything the seller revealed. "
    "Never propose an insulting lowball that ends the conversation, and never exceed "
    "fair market value. Always respond with ONLY a JSON object."
)


def negotiate_offer(
    report: VehicleReport, context: str, max_rounds: int = 4
) -> NegotiationResult:
    result = NegotiationResult()
    if not llm.available():
        result.available = False
        result.note = "LLM unavailable (install boto3 + set AWS creds), or run without --negotiate"
        return result

    market = _market_summary(report)
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"MARKET & VEHICLE:\n{market}\n\nLISTING / SELLER CONTEXT:\n{context[:4000]}\n\n"
                'Propose my opening offer. Return JSON: {"offer": int, "rationale": str}.'
            ),
        }
    ]

    last_offer: int | None = None
    for round_no in range(max_rounds):
        data, raw = llm.chat_json(_SYSTEM, messages)
        if not data or "offer" not in data:
            result.note = "model did not return a usable offer"
            break
        try:
            offer = int(data["offer"])
        except (TypeError, ValueError):
            result.note = "model returned a non-numeric offer"
            break
        held = bool(data.get("hold")) or (last_offer is not None and offer >= last_offer)
        result.rounds.append(
            {"offer": offer, "rationale": data.get("rationale", ""), "held": held}
        )

        # Stop if the model holds or stops dropping; otherwise keep pushing.
        if held:
            result.final_offer = last_offer if last_offer is not None else offer
            break
        last_offer = offer
        result.final_offer = offer

        # Keep the conversation going: append the model's turn, then push lower.
        messages.append({"role": "assistant", "content": raw or str(offer)})
        messages.append({
            "role": "user",
            "content": (
                f"That's ${offer:,}. Can you justify going even lower without an "
                "unrealistic lowball that kills the deal? If you genuinely can't, "
                'return the same number with hold=true. Return JSON: '
                '{"offer": int, "hold": bool, "rationale": str}.'
            ),
        })

    if result.final_offer:
        result.draft_message = _draft_reply(report, context, result.final_offer)
    return result


def _draft_reply(report: VehicleReport, context: str, offer: int) -> str | None:
    """A short, ready-to-send message continuing the negotiation with the seller."""
    system = (
        "Write a short message I will send a PRIVATE car seller to continue our "
        "negotiation (or to open with an offer if there's no prior chat). Tone: "
        "friendly but firm, like a normal buyer texting — NOT a form letter, and "
        "never mention any tool, report, or AI. Naturally use the concrete facts "
        "(salvage/title brand, auction record, odometer, needed repairs, market "
        f"comps) as the reason for my number, and clearly offer ${offer:,}. "
        '2-5 sentences. Return ONLY JSON: {"message": str}.'
    )
    user = f"FINDINGS:\n{_market_summary(report)}\n\nCONVERSATION SO FAR:\n{context[:4000]}"
    data, _ = llm.chat_json(system, [{"role": "user", "content": user}])
    return (data or {}).get("message") or None
