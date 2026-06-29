"""Assemble all signals into one report and render it (text or JSON)."""

from __future__ import annotations

import json
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .comps import CompsReport, get_comps
from .config import CONFIG
from .decode import DecodedVin, decode_vin
from .history import HistoryReport, get_history
from .recalls import RecallReport, SafetyRatings, get_recalls, get_safety_ratings


@dataclass
class VehicleReport:
    decoded: DecodedVin
    comps: CompsReport
    history: HistoryReport
    recalls: RecallReport
    safety: SafetyRatings | None = None
    mileage: int | None = None
    extras: dict = field(default_factory=dict)


def build_report(
    vin: str,
    mileage: int | None = None,
    statvin_fixture: Path | None = None,
    vincheck_fixture: Path | None = None,
) -> VehicleReport:
    decoded = decode_vin(vin)
    return VehicleReport(
        decoded=decoded,
        comps=get_comps(decoded, mileage=mileage),
        history=get_history(vin, statvin_fixture, vincheck_fixture),
        recalls=get_recalls(decoded),
        safety=get_safety_ratings(decoded),
        mileage=mileage,
    )


def verdict(r: VehicleReport) -> tuple[str, str]:
    """One-glance buy/pass call computed from the flags. Returns (banner, reason)."""
    h = r.history
    bad = [b for b in h.title_brands if b in _DEALBREAKER]
    rollback = bool(r.mileage and h.auction_odometer and r.mileage < h.auction_odometer - 1000)
    if bad or h.auction_status == "flagged" or rollback:
        reasons = []
        if bad:
            reasons.append(", ".join(bad) + " title")
        elif h.auction_status == "flagged":
            reasons.append("salvage auction record")
        if rollback:
            reasons.append("odometer rollback")
        return "❌ HARD PASS", "; ".join(reasons)
    if h.title_status == "clean" and h.auction_status in ("clean", "inconclusive"):
        return "✅ LOOKS CLEAN", "no title brand or auction record found (free sources)"
    return "⚠️  CAUTION", "couldn't fully verify history — capture stat.vin/vincheck to confirm"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
_VERDICT = {"clean": "✅ CLEAN", "flagged": "🚩 FLAGGED", "inconclusive": "❓ INCONCLUSIVE"}


def _money(n: int | None) -> str:
    return f"${n:,}" if n else "n/a"


def render_text(r: VehicleReport) -> str:
    d = r.decoded
    out: list[str] = []
    out.append("=" * 64)
    out.append(f"  {d.full_name or d.vin}")
    out.append(f"  VIN {d.vin}" + (f"  •  {r.mileage:,} mi" if r.mileage else ""))
    spec = "  •  ".join(p for p in (d.body_class, d.engine, d.drive_type) if p)
    if spec:
        out.append(f"  {spec}")
    out.append("=" * 64)

    # Value
    c = r.comps
    out.append("\n💵 PRIVATE-PARTY VALUE  (from real listings — asking prices)")
    if c.count:
        out.append(
            f"   {_money(c.low)}  —  {_money(c.median)} (median)  —  {_money(c.high)}"
            f"   [{c.count} comps via {', '.join(c.sources_used) or 'n/a'}]"
        )
    else:
        out.append("   no comps found")
    for note in c.notes:
        out.append(f"     · {note}")

    # History
    h = r.history
    out.append(f"\n📋 HISTORY  →  overall {_VERDICT[h.overall]}")
    out.append(f"   Title brand (NMVTIS): {_VERDICT[h.title_status]}"
               + (f"  {', '.join(h.title_brands)}" if h.title_brands else ""))
    out.append(f"   Salvage auction:      {_VERDICT[h.auction_status]}")
    for det in h.auction_details:
        out.append(f"     · {det}")
    for note in h.notes:
        out.append(f"     · {note}")

    # Recalls
    rc = r.recalls
    out.append("\n🔧 SAFETY (model-level, NHTSA)")
    if rc.error:
        out.append(f"   {rc.error}")
    else:
        cc = f", {rc.complaint_count} owner complaints" if rc.complaint_count else ""
        out.append(f"   {rc.count} open recall(s){cc}")
        for rec in rc.recalls[:5]:
            out.append(f"     · [{rec.campaign}] {rec.component}")

    out.append("\n" + "-" * 64)
    out.append("Note: free sources catch totaled/branded/salvage cars. A minor")
    out.append("repaired accident on a clean title still needs a paid Carfax/")
    out.append("AutoCheck — worth ~$3-10 only for a car you're serious about.")
    return "\n".join(out)


def render_negotiation(neg) -> str:
    out = ["\n🤝 OFFER STRATEGY  (LLM, grounded in the comps + your context)"]
    if not neg.available:
        out.append(f"   skipped — {neg.note}")
        return "\n".join(out)
    if neg.final_offer is None:
        out.append(f"   could not produce an offer — {neg.note or 'no result'}")
        return "\n".join(out)
    for i, rnd in enumerate(neg.rounds):
        tag = "HOLD" if rnd["held"] else f"round {i + 1}"
        out.append(f"   [{tag}] {_money(rnd['offer'])} — {rnd['rationale']}")
    out.append(f"\n   👉 OFFER THIS: {_money(neg.final_offer)}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Screenshot-able "card" view (KBB-style), all ASCII for crisp alignment.
# --------------------------------------------------------------------------- #
_CARD_W = 60  # inner width
_BADGE = {"clean": "[OK] CLEAN", "flagged": "[!] FLAGGED", "inconclusive": "[?] UNKNOWN"}


def _row(text: str = "") -> str:
    return "│ " + text[: _CARD_W].ljust(_CARD_W) + " │"


def _rule(left="├", right="┤") -> str:
    return left + "─" * (_CARD_W + 2) + right


def _gauge(low: int, median: int, high: int, width: int = 30) -> str:
    span = high - low
    pos = round((median - low) / span * (width - 1)) if span > 0 else 0
    pos = max(0, min(width - 1, pos))
    bar = "".join("O" if i == pos else "=" for i in range(width))
    return f"${low:,} |{bar}| ${high:,}"


# Title brands that hurt insurability / financing / resale (your dad's rule).
_DEALBREAKER = {"salvage", "rebuilt", "flood", "junk", "fire", "lemon", "total loss"}


def _watchouts(r: VehicleReport) -> list[str]:
    h, out = r.history, []
    bad = [b for b in h.title_brands if b in _DEALBREAKER]
    if bad or h.auction_status == "flagged":
        out.append("Branded/totaled title (" + (", ".join(bad) or "salvage auction")
                   + "): insurers often refuse full coverage, banks won't finance, "
                   "~20-40% less resale.")
    # Odometer rollback: a past auction reading higher than the car's current miles.
    if r.mileage and h.auction_odometer and r.mileage < h.auction_odometer - 1000:
        out.append(f"Odometer rollback? auction showed {h.auction_odometer:,} mi but "
                   f"listing says {r.mileage:,} mi.")
    return out


def _sources(r: VehicleReport) -> list[str]:
    vin, h = r.decoded.vin, r.history
    src = [f"auction/title: stat.vin/cars/{vin}"]
    if h.auction_lot:
        src.append(f"copart lot {h.auction_lot}: copart.com/lot/{h.auction_lot}")
    if h.auction_photos:
        extra = f" (+{len(h.auction_photos) - 1} more)" if len(h.auction_photos) > 1 else ""
        src.append(f"damage photo: {h.auction_photos[0]}{extra}")
    src.append(f"title brands: vincheck.info  ·  recalls: nhtsa.gov/recalls (VIN {vin})")
    return src


def render_card(r: VehicleReport) -> str:
    """The shareable, fact-only card (no LLM offer). Backed by public records."""
    d, c, h, rc = r.decoded, r.comps, r.history, r.recalls
    L = []
    # Verdict banner ABOVE the box (emoji-safe; instantly readable in a screenshot)
    banner, reason = verdict(r)
    L.append("")
    L.append(f"  {banner}")
    for line in textwrap.wrap(reason, _CARD_W):
        L.append(f"  {line}")
    L.append(_rule("┌", "┐"))
    L.append(_row(d.full_name or d.vin))
    sub = "   ".join(p for p in (d.vin, f"{r.mileage:,} mi" if r.mileage else "", d.engine) if p)
    L.append(_row(sub))

    # Value
    L.append(_rule())
    L.append(_row("PRIVATE-PARTY VALUE"))
    if c.count and c.low and c.high:
        L.append(_row("  " + _gauge(c.low, c.median, c.high)))
        L.append(_row(f"          median  {_money(c.median)}   ·   {c.count} listings "
                      f"near {CONFIG.home_zip}"))
    else:
        L.append(_row("  no live comps yet — add AUTODEV_API_KEY (free) for a range"))

    # History + safety
    L.append(_rule())
    L.append(_row(f"HISTORY      {_BADGE[h.overall]}"
                  + (f"   {', '.join(h.title_brands)}" if h.title_brands else "")))
    L.append(_row(f"  title(NMVTIS) {_BADGE[h.title_status].split(']')[0]}]"
                  f"    salvage-auction {_BADGE[h.auction_status].split(']')[0]}]"))
    for line in textwrap.wrap(" · ".join(h.auction_details), _CARD_W - 2)[:3]:
        L.append(_row("  " + line))
    safety = rc.error or (f"{rc.count} open recalls"
                          + (f",  {rc.complaint_count} complaints" if rc.complaint_count else ""))
    if r.safety and r.safety.overall:
        safety += f"  ·  NCAP {r.safety.overall}/5"
    L.append(_row(f"SAFETY       {safety}"))

    # Watch-outs (deterministic, fact-based — safe to show a seller)
    watch = _watchouts(r)
    if watch:
        L.append(_rule())
        L.append(_row("WATCH-OUTS"))
        for w in watch:
            wrapped = textwrap.wrap(w, _CARD_W - 4)
            for j, line in enumerate(wrapped):
                L.append(_row(("  ! " if j == 0 else "    ") + line))

    # Sources — so the number's credibility is the public record, not the tool
    L.append(_rule())
    L.append(_row("SOURCES (anyone can look these up by VIN)"))
    for s in _sources(r):
        for line in textwrap.wrap(s, _CARD_W - 4):
            L.append(_row("  " + line))
    L.append(_rule("└", "┘"))
    return "\n".join(L)


def render_offer_private(neg) -> str:
    """Your eyes only — keep this OUT of any screenshot you send the seller."""
    if neg is None or not getattr(neg, "final_offer", None):
        return ""
    L = ["", "🔒 PRIVATE — your offer cheat-sheet (do NOT screenshot/send this)"]
    for i, rnd in enumerate(neg.rounds):
        tag = "hold" if rnd["held"] else f"round {i + 1}"
        L.append(f"   [{tag}] {_money(rnd['offer'])} — {rnd['rationale']}")
    L.append(f"\n   >> OFFER THIS: {_money(neg.final_offer)}")
    return "\n".join(L)


def render_json(r: VehicleReport) -> str:
    def encode(o):
        try:
            return asdict(o)
        except TypeError:
            return str(o)

    banner, reason = verdict(r)
    payload = {
        "vin": r.decoded.vin,
        "vehicle": r.decoded.full_name,
        "mileage": r.mileage,
        "verdict": banner, "verdict_reason": reason,
        "decoded": {k: v for k, v in asdict(r.decoded).items() if k != "raw"},
        "value": {
            "low": r.comps.low, "median": r.comps.median, "high": r.comps.high,
            "comp_count": r.comps.count, "sources": r.comps.sources_used,
            "notes": r.comps.notes,
        },
        "history": asdict(r.history) | {"overall": r.history.overall},
        "recalls": {
            "count": r.recalls.count, "complaints": r.recalls.complaint_count,
            "items": [asdict(x) for x in r.recalls.recalls], "error": r.recalls.error,
        },
        "safety": asdict(r.safety) if r.safety else None,
    }
    return json.dumps(payload, indent=2, default=str)
