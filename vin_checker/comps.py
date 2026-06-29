"""Private-party value range from REAL listings ("comps").

Instead of trusting a single KBB/Edmunds opinion, we gather actual cars for sale
matching the decoded year/make/model near the user's ZIP and compute the spread.
This is the DIY version of CarGurus' Instant Market Value, and for "what will it
actually trade at near me" it's generally more honest than a national model.

Sources, in priority order:
  1. MarketCheck active-listings API  (free tier: 500/mo, needs key)
  2. Craigslist                       (best-effort, no key)

A note on accuracy: these are ASKING prices, which run a bit above sold prices,
so treat the median as a ceiling-ish anchor and negotiate down.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

import requests

from . import http_cache
from .config import CONFIG
from .decode import DecodedVin

AUTODEV_URL = "https://api.auto.dev/listings"
MARKETCHECK_URL = "https://mc-api.marketcheck.com/v2/search/car/active"


def _dig(obj: dict, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _to_int(val) -> int | None:
    digits = re.sub(r"[^0-9]", "", str(val or ""))
    return int(digits) if digits else None


@dataclass
class Comp:
    price: int
    miles: int | None
    trim: str | None
    source: str
    url: str | None = None


@dataclass
class CompsReport:
    comps: list[Comp] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.comps)

    @property
    def prices(self) -> list[int]:
        return sorted(c.price for c in self.comps if c.price)

    def _pct(self, p: float) -> int | None:
        prices = self.prices
        if not prices:
            return None
        if len(prices) == 1:
            return prices[0]
        # statistics.quantiles gives cut points; index into them.
        cuts = statistics.quantiles(prices, n=100, method="inclusive")
        return int(cuts[min(int(p) - 1, len(cuts) - 1)])

    @property
    def low(self) -> int | None:
        return self._pct(10)

    @property
    def median(self) -> int | None:
        prices = self.prices
        return int(statistics.median(prices)) if prices else None

    @property
    def high(self) -> int | None:
        return self._pct(90)


def _autodev_comps(
    decoded: DecodedVin, mileage: int | None, report: CompsReport
) -> None:
    if not CONFIG.autodev_api_key:
        report.notes.append("Auto.dev skipped (no AUTODEV_API_KEY set)")
        return

    params = {"vehicle.make": decoded.make, "vehicle.model": decoded.model}
    if decoded.year:
        params["vehicle.year"] = decoded.year
    try:
        listings = http_cache.get_json(
            AUTODEV_URL, params=params,
            headers={"Authorization": f"Bearer {CONFIG.autodev_api_key}"},
            ttl=21600,  # 6h — re-checking a car won't burn the 1,000/mo quota
        ).get("data") or []
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        report.notes.append(f"Auto.dev rate-limited ({code})" if code == 429
                            else f"Auto.dev error: {e}")
        return
    except (requests.RequestException, ValueError) as e:
        report.notes.append(f"Auto.dev error: {e}")
        return

    added = 0
    for ln in listings:
        price = _to_int(_dig(ln, "retailListing", "price") or ln.get("price"))
        if not price:
            continue
        report.comps.append(
            Comp(
                price=price,
                miles=_to_int(_dig(ln, "retailListing", "miles")),
                trim=_dig(ln, "vehicle", "trim"),
                source="auto.dev",
                url=_dig(ln, "retailListing", "vdp") or _dig(ln, "retailListing", "vdpUrl"),
            )
        )
        added += 1

    if added:
        report.sources_used.append("auto.dev")
    report.notes.append(f"Auto.dev: {added} comps")


def _marketcheck_comps(
    decoded: DecodedVin, mileage: int | None, report: CompsReport
) -> None:
    if not CONFIG.marketcheck_api_key:
        report.notes.append("MarketCheck skipped (no MARKETCHECK_API_KEY set)")
        return

    params = {
        "api_key": CONFIG.marketcheck_api_key,
        "year": decoded.year,
        "make": decoded.make,
        "model": decoded.model,
        "zip": CONFIG.home_zip,
        "radius": CONFIG.search_radius_miles,
        "rows": 50,
        "car_type": "used",
    }
    if mileage:  # tighten to +/- 20k miles around the subject car
        params["miles_range"] = f"{max(0, mileage - 20000)}-{mileage + 20000}"

    try:
        resp = requests.get(params=params, url=MARKETCHECK_URL, timeout=CONFIG.http_timeout)
        resp.raise_for_status()
        listings = resp.json().get("listings") or []
    except (requests.RequestException, ValueError) as e:
        report.notes.append(f"MarketCheck error: {e}")
        return

    added = 0
    for ln in listings:
        price = ln.get("price")
        if not price:
            continue
        build = ln.get("build") or {}
        report.comps.append(
            Comp(
                price=int(price),
                miles=ln.get("miles"),
                trim=build.get("trim"),
                source="marketcheck",
                url=ln.get("vdp_url"),
            )
        )
        added += 1

    if added:
        report.sources_used.append("marketcheck")
    report.notes.append(f"MarketCheck: {added} comps")


def _craigslist_comps(decoded: DecodedVin, report: CompsReport) -> None:
    """Best-effort. Craigslist now renders results via JS, so the legacy HTML
    scrape is unreliable; we attempt it and degrade gracefully. If this comes
    back empty in practice, capture a results-page HTML sample and we'll wire a
    parser against it in vin_checker/history.py-style fixtures."""
    query = f"{decoded.year} {decoded.make} {decoded.model}".strip()
    url = "https://chattanooga.craigslist.org/search/cta"
    try:
        resp = requests.get(
            url,
            params={"query": query, "auto_title_status": 1},
            timeout=CONFIG.http_timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        report.notes.append(f"Craigslist error: {e}")
        return

    # Modern Craigslist embeds results as JSON or JS-rendered nodes; the classic
    # <li class="result-row"> is mostly gone. We parse defensively and treat a
    # zero result as "needs an HTML sample" rather than an error.
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    added = 0
    for node in soup.select(".result-row, li.cl-static-search-result"):
        price_el = node.select_one(".result-price, .price")
        if not price_el:
            continue
        try:
            price = int(price_el.get_text(strip=True).replace("$", "").replace(",", ""))
        except ValueError:
            continue
        link = node.select_one("a")
        report.comps.append(
            Comp(price=price, miles=None, trim=None, source="craigslist",
                 url=link.get("href") if link else None)
        )
        added += 1

    if added:
        report.sources_used.append("craigslist")
        report.notes.append(f"Craigslist: {added} comps")
    else:
        report.notes.append(
            "Craigslist: 0 comps (JS-rendered page; capture an HTML sample to "
            "enable a real parser)"
        )


def _refine_comps(report: CompsReport, decoded: DecodedVin, mileage: int | None) -> None:
    """Make the median trustworthy: keep same-ish trim, a mileage band, and drop
    price outliers. Each filter only applies if it leaves a usable sample."""
    comps = report.comps

    if decoded.trim:
        tok = decoded.trim.split()[0].lower()  # e.g. "Premium" → "premium"
        same = [c for c in comps if c.trim and tok in c.trim.lower()]
        if len(same) >= 5:
            comps = same
            report.notes.append(f"refined to trim~{tok}: {len(same)}")

    if mileage:
        band = [c for c in comps if c.miles and abs(c.miles - mileage) <= 30000]
        if len(band) >= 5:
            comps = band
            report.notes.append(f"refined to ±30k mi: {len(band)}")

    prices = sorted(c.price for c in comps)
    if len(prices) >= 8:
        q1, q3 = statistics.quantiles(prices, n=4, method="inclusive")[::2]
        lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        comps = [c for c in comps if lo <= c.price <= hi]

    report.comps = comps


def get_comps(decoded: DecodedVin, mileage: int | None = None) -> CompsReport:
    report = CompsReport()
    _autodev_comps(decoded, mileage, report)      # free, no credit card (preferred)
    if not report.comps:
        _marketcheck_comps(decoded, mileage, report)
        _craigslist_comps(decoded, report)
    _refine_comps(report, decoded, mileage)
    if not report.comps:
        report.notes.append(
            "No comps found. Add a MARKETCHECK_API_KEY for reliable value data."
        )
    return report
