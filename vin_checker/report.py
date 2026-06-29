"""Assemble all signals into one report and render it (text or JSON)."""

from __future__ import annotations

import json
import re
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
    progress=None,
) -> VehicleReport:
    p = progress or (lambda *_: None)
    p("decoding VIN")
    decoded = decode_vin(vin)
    p("finding comparable listings")
    comps = get_comps(decoded, mileage=mileage)
    p("checking title + salvage history")
    history = get_history(vin, statvin_fixture, vincheck_fixture, decoded=decoded, progress=progress)
    p("checking recalls + safety ratings")
    recalls = get_recalls(decoded)
    safety = get_safety_ratings(decoded)
    return VehicleReport(
        decoded=decoded, comps=comps, history=history,
        recalls=recalls, safety=safety, mileage=mileage,
    )


def verdict(r: VehicleReport) -> tuple[str, str]:
    """One-glance call from the flags. Four states, by what we actually found:
      ❌ HARD PASS   — deal-breaker found (salvage/rebuilt/etc., auction, rollback)
      ⚠️ CAUTION     — real but non-fatal issue found (theft/accident, minor brand)
      ✅ LOOKS CLEAN — verified: no brand/theft/accident on record
      ❓ UNVERIFIED  — couldn't check history (no capture) — *not* a caution
    """
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

    # Real findings that aren't deal-breakers → caution (an issue, not missing data).
    caution = [b for b in h.title_brands if b not in _DEALBREAKER]  # e.g. hail
    for n in h.notes:
        nl = n.lower()
        if "theft" in nl and "record" in nl:
            caution.append("theft record")
        elif "accident" in nl and "record" in nl:
            caution.append("prior accident record")
    if caution:
        return "⚠️ CAUTION", "; ".join(dict.fromkeys(caution))

    if h.title_status == "clean":
        return "✅ LOOKS CLEAN", "no title brand, theft, or accident record found"
    return "❓ UNVERIFIED", "title/salvage history not checked — capture stat.vin/vincheck"


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
    if neg.current_state:
        out.append(f"   where it stands: {neg.current_state}")
    label = "DEAL AGREED" if getattr(neg, "deal_agreed", False) else "OFFER"
    out.append(f"   👉 {label}: {_money(neg.final_offer)}")
    if neg.rationale:
        out.append(f"   why: {neg.rationale}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Screenshot-able "card" view (KBB-style), all ASCII for crisp alignment.
# --------------------------------------------------------------------------- #
_CARD_W = 60  # inner width

# --- ANSI color (auto-disabled when output isn't a TTY; see set_color) --------
_COLOR = True
RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
RED, GRN, YEL, CYAN, WHITE = "\x1b[31m", "\x1b[32m", "\x1b[33m", "\x1b[36m", "\x1b[97m"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def set_color(enabled: bool) -> None:
    global _COLOR
    _COLOR = enabled


def paint(s: str, *codes: str) -> str:
    return ("".join(codes) + s + RESET) if (_COLOR and codes) else s


def _vis(s: str) -> str:  # visible length, ignoring color codes (for padding)
    return _ANSI_RE.sub("", s)


def _row(text: str = "") -> str:
    vis = _vis(text)
    if len(vis) > _CARD_W:  # overflow: drop color and clip
        text = vis = vis[:_CARD_W]
    bar = paint("│", DIM)
    return f"{bar} {text}{' ' * (_CARD_W - len(vis))} {bar}"


def _rule(left="├", right="┤") -> str:
    return paint(left + "─" * (_CARD_W + 2) + right, DIM)


_BADGE_FULL = {"clean": (GRN, BOLD), "flagged": (RED, BOLD), "inconclusive": (DIM,)}
_BADGE_TEXT = {"clean": "[OK] CLEAN", "flagged": "[!] FLAGGED", "inconclusive": "[?] UNKNOWN"}


def _badge(status: str) -> str:
    return paint(_BADGE_TEXT[status], *_BADGE_FULL[status])


def _badge_short(status: str) -> str:
    short = {"clean": "[OK]", "flagged": "[!]", "inconclusive": "[?]"}[status]
    return paint(short, *_BADGE_FULL[status])


def _gauge(low: int, median: int, high: int, width: int = 30) -> str:
    span = high - low
    pos = max(0, min(width - 1, round((median - low) / span * (width - 1)) if span > 0 else 0))
    seg = max(1, width // 3)
    cells = []
    for i in range(width):
        if i == pos:
            cells.append(paint("O", BOLD, WHITE))
        elif i < seg:
            cells.append(paint("=", RED))       # low / underpriced
        elif i < 2 * seg:
            cells.append(paint("=", GRN))       # fair-value sweet spot
        else:
            cells.append(paint("=", DIM))       # high / overpriced
    return f"{paint('$' + format(low, ','), DIM)} |{''.join(cells)}| {paint('$' + format(high, ','), DIM)}"


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
    color = (RED if "HARD PASS" in banner else YEL if "CAUTION" in banner
             else GRN if "LOOKS CLEAN" in banner else DIM)
    L.append("")
    L.append("  " + paint(banner, color, BOLD))
    for line in textwrap.wrap(reason, _CARD_W):
        L.append("  " + paint(line, DIM))
    L.append(_rule("┌", "┐"))
    L.append(_row(paint(d.full_name or d.vin, BOLD)))
    sub = "   ".join(p for p in (d.vin, f"{r.mileage:,} mi" if r.mileage else "", d.engine) if p)
    L.append(_row(paint(sub, DIM)))
    specs = "  ·  ".join(p for p in (
        d.body_class, f"{d.doors}dr" if d.doors else "", f"{d.hp} hp" if d.hp else "",
        d.drive_type, d.transmission) if p)
    if specs:
        L.append(_row(paint(specs, DIM)))

    # Value
    L.append(_rule())
    L.append(_row(paint("PRIVATE-PARTY VALUE", BOLD, CYAN)))
    if c.count and c.low and c.high:
        L.append(_row("  " + _gauge(c.low, c.median, c.high)))
        L.append(_row(f"          median  {paint(_money(c.median), GRN, BOLD)}   ·   "
                      f"{c.count} listings near {CONFIG.home_zip}"))
    else:
        L.append(_row(paint("  no live comps yet — add AUTODEV_API_KEY (free) for a range", DIM)))

    # History + safety
    L.append(_rule())
    L.append(_row(paint("HISTORY", BOLD, CYAN) + f"      {_badge(h.overall)}"
                  + (f"   {paint(', '.join(h.title_brands), RED)}" if h.title_brands else "")))
    L.append(_row(f"  title(NMVTIS) {_badge_short(h.title_status)}"
                  f"    salvage-auction {_badge_short(h.auction_status)}"))
    for line in textwrap.wrap(" · ".join(h.auction_details), _CARD_W - 2)[:3]:
        L.append(_row("  " + paint(line, RED)))
    parts = ["recalls n/a" if rc.error else f"{rc.count} open recalls"]
    if rc.complaint_count:
        parts.append(f"{rc.complaint_count} complaints")
    safety_row = paint("SAFETY", BOLD, CYAN) + "       " + ",  ".join(parts)
    if r.safety and r.safety.overall:
        safety_row += "  ·  " + paint(f"NCAP {r.safety.overall}/5", GRN)
    L.append(_row(safety_row))

    # Watch-outs (deterministic, fact-based — safe to show a seller)
    watch = _watchouts(r)
    if watch:
        L.append(_rule())
        L.append(_row(paint("WATCH-OUTS", BOLD, YEL)))
        for w in watch:
            wrapped = textwrap.wrap(w, _CARD_W - 4)
            for j, line in enumerate(wrapped):
                prefix = paint("  ! ", RED, BOLD) if j == 0 else "    "
                L.append(_row(prefix + paint(line, YEL)))

    # Sources — so the number's credibility is the public record, not the tool
    L.append(_rule())
    L.append(_row(paint("SOURCES (anyone can look these up by VIN)", BOLD, CYAN)))
    for s in _sources(r):
        for line in textwrap.wrap(s, _CARD_W - 4):
            L.append(_row(paint("  " + line, DIM)))
    L.append(_rule("└", "┘"))
    return "\n".join(L)


def render_distance(dist) -> str:
    if not dist:
        return ""
    miles = dist.get("drive_mi") or dist.get("straight_mi")
    kind = "drive" if dist.get("drive_mi") else "straight-line"
    t = dist.get("drive_min")
    when = f", ~{t // 60}h{t % 60:02d}" if t else ""
    return "\n" + paint(f"📍 ~{miles} mi {kind} from {CONFIG.home_name}  ·  "
                        f"{dist.get('place','')}{when}", BOLD, CYAN)


def render_research(res) -> str:
    """Web-grounded specs/0-60/audio/problems + an in-person inspection checklist."""
    if res is None or not getattr(res, "available", False):
        return ""
    L = ["", paint("🔧 SPECS & RESEARCH  (web-grounded — verify on the car)", BOLD, CYAN)]

    def kv(label, val):
        if not val:
            return
        wrapped = textwrap.wrap(f"{label}: {val}", _CARD_W + 8)
        for j, line in enumerate(wrapped):
            L.append("   " + (line if j == 0 else "    " + line))

    kv("0-60", res.zero_to_sixty)
    kv("drive", res.performance)
    kv("audio", res.audio)
    kv("bluetooth", res.connectivity)
    if res.common_problems:
        L.append("   " + paint("common problems", BOLD, YEL))
        for x in res.common_problems:
            for j, line in enumerate(textwrap.wrap(x, _CARD_W)):
                L.append("   " + (paint("- ", YEL, BOLD) if j == 0 else "    ") + line)
    if res.inspect_in_person:
        L.append("")
        L.append(paint("🔎 WHAT TO CHECK IN PERSON", BOLD, CYAN))
        for x in res.inspect_in_person:
            for j, line in enumerate(textwrap.wrap(x, _CARD_W + 4)):
                L.append("   " + (paint("[ ] ", CYAN, BOLD) if j == 0 else "    ") + line)
    if res.sources:
        L.append("   " + paint("sources: " + "  ".join(res.sources[:3]), DIM))
    return "\n".join(L)


def render_proscons(pros: list[str], cons: list[str]) -> str:
    """Buyer-facing decision aid (your call) — not part of the shareable card."""
    if not pros and not cons:
        return ""
    L = ["", paint("⚖️  PROS / CONS  (your call)", BOLD, CYAN)]
    if pros:
        L.append("   " + paint("PROS", BOLD, GRN))
        for x in pros:
            wrapped = textwrap.wrap(x, _CARD_W)
            for j, line in enumerate(wrapped):
                L.append("   " + (paint("+ ", GRN, BOLD) if j == 0 else "  ") + line)
    if cons:
        L.append("   " + paint("CONS", BOLD, RED))
        for x in cons:
            wrapped = textwrap.wrap(x, _CARD_W)
            for j, line in enumerate(wrapped):
                L.append("   " + (paint("- ", RED, BOLD) if j == 0 else "  ") + line)
    return "\n".join(L)


def render_draft(neg) -> str:
    """Seller-facing message — safe to send (no AI/offer-math mentioned)."""
    if neg is None or not getattr(neg, "draft_message", None):
        return ""
    L = ["", paint("✉️  DRAFT MESSAGE TO SELLER  (review/tweak, then send)", BOLD, CYAN),
         "   " + paint("-" * 56, DIM)]
    for line in textwrap.wrap(neg.draft_message, _CARD_W + 2):
        L.append("   " + line)
    return "\n".join(L)


def render_offer_private(neg) -> str:
    """Your eyes only — keep this OUT of any screenshot you send the seller."""
    if neg is None or not getattr(neg, "final_offer", None):
        return ""
    L = ["", paint("🔒 PRIVATE — your move (do NOT screenshot/send this)", DIM)]
    label = "DEAL AGREED" if getattr(neg, "deal_agreed", False) else "OFFER"
    L.append("   " + paint(f">> {label}: {_money(neg.final_offer)}", GRN, BOLD))
    if getattr(neg, "current_state", None):
        for line in textwrap.wrap(f"where it stands: {neg.current_state}", _CARD_W + 2):
            L.append("   " + paint(line, DIM))
    if neg.rationale:
        for line in textwrap.wrap(f"why: {neg.rationale}", _CARD_W + 2):
            L.append("   " + paint(line, DIM))
    return "\n".join(L)


def render_diagnostics(r: VehicleReport) -> str:
    """Why did it say what it said — for testing/debugging."""
    L = ["DIAGNOSTICS", f"  decode: {r.decoded.full_name}  (trim={r.decoded.trim})"]
    L.append(f"  comps: {r.comps.count} after refine · sources={r.comps.sources_used or '[]'}")
    for n in r.comps.notes:
        L.append(f"      · {n}")
    L.append(f"  history: title={r.history.title_status} · auction={r.history.auction_status}"
             f" · lot={r.history.auction_lot} · photos={len(r.history.auction_photos)}")
    for n in r.history.notes:
        L.append(f"      · {n}")
    if r.recalls.error:
        L.append(f"  recalls error: {r.recalls.error}")
    if r.safety and r.safety.error:
        L.append(f"  safety: {r.safety.error}")
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
