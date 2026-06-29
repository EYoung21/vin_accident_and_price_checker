"""Free history signals: salvage-auction record + NMVTIS title brand.

Two independent free channels that together catch the deal-killers (totaled,
salvage, flood, branded title). What they CANNOT see is a minor repaired accident
on a still-clean title -- that remains paid Carfax/AutoCheck territory.

  - stat.vin      -> Copart/IAAI salvage-auction records (damage, odometer, sale)
  - vincheck.info -> NMVTIS title brands (salvage / rebuilt / flood / junk / lemon)

Both sites render results client-side (JS) behind CSRF/bot protection, so a plain
HTTP fetch reaches the page shell but not always the rendered report. Each client
therefore supports a FIXTURE path: drop a captured results-page HTML into
automation_html/<source>/ and the parser runs against it offline. Capture flow:
load the VIN result page in a browser -> "Save Page As" (or copy the rendered
DOM) -> save to the fixture path printed below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from .config import CONFIG

try:
    from curl_cffi import requests as cr  # browser-grade TLS impersonation
except ImportError:  # fall back to plain requests (more likely to be blocked)
    import requests as cr  # type: ignore

# Captured rendered HTML lives here, in per-source subfolders. Drop a file named
# <VIN>.html into the right subfolder and it's auto-detected (no flags needed).
_AUTOMATION_DIR = Path(__file__).resolve().parent.parent / "automation_html"
STATVIN_DIR = _AUTOMATION_DIR / "statvin"
VINCHECK_DIR = _AUTOMATION_DIR / "vincheck"

# Title-brand keywords we treat as red flags if present in NMVTIS results.
BRAND_KEYWORDS = [
    "salvage", "rebuilt", "reconstructed", "flood", "water damage", "junk",
    "lemon", "fire", "hail", "total loss", "odometer", "rollback",
]
# Markers that mean "the page returned only the JS shell, not the report".
SHELL_MARKERS = ["loading...", "please wait", "verifying you are human"]


@dataclass
class HistoryReport:
    # status: "clean" | "flagged" | "inconclusive"
    auction_status: str = "inconclusive"
    auction_details: list[str] = field(default_factory=list)
    auction_lot: str | None = None       # Copart/IAAI lot # → source link
    auction_odometer: int | None = None  # odometer at auction → rollback check
    auction_photos: list[str] = field(default_factory=list)  # damage photo URLs
    title_status: str = "inconclusive"
    title_brands: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def overall(self) -> str:
        if "flagged" in (self.auction_status, self.title_status):
            return "flagged"
        if self.auction_status == "clean" and self.title_status == "clean":
            return "clean"
        return "inconclusive"


def _looks_like_shell(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in SHELL_MARKERS) and len(
        BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    ) < 4000


# --------------------------------------------------------------------------- #
# stat.vin  (salvage auction)
# --------------------------------------------------------------------------- #
def _statvin_fetch(vin: str) -> str | None:
    """Returns result HTML using the verified Laravel CSRF + session POST flow,
    or None on failure. Live results are often JS-rendered (see module docstring)."""
    try:
        s = cr.Session(impersonate="chrome")  # type: ignore[call-arg]
    except TypeError:  # plain requests fallback has no impersonate kwarg
        s = cr.Session()
    try:
        home = s.get("https://stat.vin/", timeout=CONFIG.http_timeout)
        m = re.search(
            r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)', home.text
        )
        if not m:
            return None
        r = s.post(
            "https://stat.vin/car-search",
            data={"vin": vin, "_token": m.group(1)},
            timeout=CONFIG.http_timeout + 10,
        )
        return r.text if r.status_code == 200 else None
    except Exception:  # network/TLS/cloudflare -- non-fatal
        return None


# Auction record fields stat.vin renders as `.car-box-option` label + sibling value.
_STATVIN_FIELDS = ["Auction", "Damage", "Odometer, mi", "Retail value", "Location",
                   "Lot number"]


def _parse_statvin(html: str, report: HistoryReport, vin: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # A real VIN-specific result echoes the VIN. If it doesn't (JS shell, generic
    # marketing page, or bot wall), we MUST NOT issue a verdict from boilerplate.
    if _looks_like_shell(html) or vin.lower() not in text.lower():
        report.auction_status = "inconclusive"
        report.notes.append(
            "stat.vin did not return a VIN-specific result (JS-rendered/bot-walled); "
            f"capture the rendered result HTML to {STATVIN_DIR/'<VIN>.html'} "
            "for a definitive read"
        )
        return

    # Each datum is a `.car-box-option` label whose value sits in the next sibling.
    fields: dict[str, str] = {}
    for opt in soup.select(".car-box-option"):
        label = opt.get_text(" ", strip=True).rstrip(":").strip()
        sib = opt.find_next_sibling()
        value = sib.get_text(" ", strip=True) if sib else ""
        if label and value:
            fields[label.lower()] = value

    # An auction lot record means the car was at Copart/IAAI — a strong wreck signal.
    if any(k.lower() in fields for k in ("damage", "lot number", "auction")):
        report.auction_status = "flagged"
        report.auction_lot = re.sub(r"\D", "", fields.get("lot number", "")) or None
        report.auction_odometer = (
            int(d) if (d := re.sub(r"\D", "", fields.get("odometer, mi", ""))) else None
        )
        # stat.vin damage photos for THIS car end in the lot number in the URL path.
        if report.auction_lot:
            photos = re.findall(
                rf"https?://cdn\d+\.stat\.vin/\S+?/{report.auction_lot}\b", html)
            report.auction_photos = list(dict.fromkeys(photos))[:5]
        for key in _STATVIN_FIELDS:
            if (v := fields.get(key.lower())):
                report.auction_details.append(f"{key}: {v}")
        if (fb := re.search(r"final bid:?\s*\$?\s*([\d ,.]+)", text, re.I)):
            bid = re.sub(r"[ ,]", "", fb.group(1)).rstrip(".")
            sold = " (SOLD)" if "has been sold" in text.lower() else ""
            report.auction_details.append(f"Final bid: ${bid}{sold}")
    else:
        # VIN present but no lot fields: can't confirm a clean record from this page.
        report.auction_status = "inconclusive"
        report.notes.append("stat.vin: no auction lot fields found for this VIN")


# --------------------------------------------------------------------------- #
# vincheck.info  (NMVTIS title brand)
# --------------------------------------------------------------------------- #
def _vincheck_fetch(vin: str) -> str | None:
    try:
        s = cr.Session(impersonate="chrome")  # type: ignore[call-arg]
    except TypeError:
        s = cr.Session()
    for url in (f"https://vincheck.info/vehicle/{vin}", f"https://vincheck.info/?vin={vin}"):
        try:
            r = s.get(url, timeout=CONFIG.http_timeout)
            if r.status_code == 200 and len(r.text) > 1000:
                return r.text
        except Exception:
            continue
    return None


# vincheck.info renders each check as a small card with a green (clean) or red
# (records found) indicator and a leading count. Title-killing brands vs other signals:
_TITLE_BRANDS = ("salvage", "flood", "junk", "total loss", "title brand", "lemon",
                 "rebuilt", "fire", "hail")
_OTHER_CHECKS = ("theft", "accident")
_SECTION_RE = re.compile(
    r"(salvage|flood|junk|total loss|title brand|lemon|rebuilt|theft|accident)", re.I)
_COLOR_RE = re.compile(r"(text|bg)-(red|green)-500")


def _parse_vincheck(html: str, report: HistoryReport, vin: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    if _looks_like_shell(html) or vin.lower() not in text.lower():
        report.title_status = "inconclusive"
        report.notes.append(
            "vincheck.info did not return a VIN-specific result (JS-rendered/"
            f"marketing page); capture rendered HTML to {VINCHECK_DIR/'<VIN>.html'} "
            "for a definitive read"
        )
        return

    # Walk each "<Label> Check" card up to the ancestor that carries a red/green badge.
    statuses: dict[str, str] = {}
    for node in soup.find_all(string=_SECTION_RE):
        card = node.parent
        for _ in range(5):
            if card is None:
                break
            if card.find(class_=_COLOR_RE):
                break
            card = card.parent
        if card is None:
            continue
        ctext = card.get_text(" ", strip=True)
        m = _SECTION_RE.search(ctext)
        if not m or len(ctext) > 160:
            continue
        label = m.group(1).lower()
        if label in statuses:
            continue
        is_red = bool(card.find(class_=re.compile(r"(text|bg)-red-500")))
        is_green = bool(card.find(class_=re.compile(r"(text|bg)-green-500")))
        statuses[label] = "flag" if (is_red and not is_green) else (
            "clean" if (is_green and not is_red) else "unknown")

    brand_flags = sorted(k for k in statuses if k in _TITLE_BRANDS and statuses[k] == "flag")
    if brand_flags:
        report.title_status = "flagged"
        report.title_brands = brand_flags
    elif any(statuses.get(k) == "clean" for k in _TITLE_BRANDS):
        report.title_status = "clean"
    else:
        report.title_status = "inconclusive"

    for k in _OTHER_CHECKS:
        if statuses.get(k) == "flag":
            report.notes.append(f"vincheck: {k} record(s) found")


# --------------------------------------------------------------------------- #
def get_history(vin: str, statvin_fixture: Path | None = None,
                vincheck_fixture: Path | None = None) -> HistoryReport:
    report = HistoryReport()
    # Frictionless capture: drop <VIN>.html into automation_html/statvin|vincheck/
    # and it's picked up automatically — no --statvin-fixture/--vincheck-fixture needed.
    if statvin_fixture is None and (f := STATVIN_DIR / f"{vin}.html").exists():
        statvin_fixture = f
    if vincheck_fixture is None and (f := VINCHECK_DIR / f"{vin}.html").exists():
        vincheck_fixture = f

    s_html = statvin_fixture.read_text(errors="ignore") if statvin_fixture else _statvin_fetch(vin)
    if s_html:
        _parse_statvin(s_html, report, vin)
    else:
        report.notes.append("stat.vin unreachable (network or bot protection)")

    v_html = vincheck_fixture.read_text(errors="ignore") if vincheck_fixture else _vincheck_fetch(vin)
    if v_html:
        _parse_vincheck(v_html, report, vin)
    else:
        report.notes.append("vincheck.info unreachable (network or bot protection)")

    return report
