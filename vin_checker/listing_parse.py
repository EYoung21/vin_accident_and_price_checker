"""Parse a pasted Marketplace/Craigslist listing into structured fields.

Regex handles the deterministic bits (VIN, mileage, price) for free. The optional
LLM layer adds the fuzzy stuff a regex can't: seller claims, condition, and red
flags ("dodged the accident question", "salvage rebuilt", "needs work"). This is
the recommended path vs. automating Marketplace itself, which gets your account
banned within hours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .decode import VIN_RE
from . import llm

# Find a 17-char VIN anywhere in free text (word-boundaried).
_VIN_IN_TEXT = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b", re.IGNORECASE)
_MILEAGE = re.compile(
    r"(\d{1,3}(?:,\d{3})|\d{2,6})\s*k?\s*(?:miles|mi|mileage|odometer|k\s*mi)",
    re.IGNORECASE,
)
_PRICE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,6})")
# The LIVE Marketplace price is the headline shown right before "Listed ... ago".
# Prices written inside the seller's description are often the ORIGINAL/stale price.
_HEADLINE_PRICE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+|\d{3,6})\s*\n?\s*Listed\b", re.IGNORECASE)


def current_listing_price(text: str) -> int | None:
    """The seller's CURRENT asking price: the Marketplace headline ('$X … Listed X
    ago') if present, else the first price in the paste (the headline is usually
    first). Deliberately ignores higher prices buried in the description."""
    m = _HEADLINE_PRICE.search(text) or _PRICE.search(text)
    return int(m.group(1).replace(",", "")) if m else None


@dataclass
class ParsedListing:
    vin: str | None = None
    mileage: int | None = None
    price: int | None = None
    seller_claims: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    used_llm: bool = False


def _regex_mileage(text: str) -> int | None:
    m = _MILEAGE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    val = int(raw)
    # "95k miles" style
    if "k" in m.group(0).lower() and val < 1000:
        val *= 1000
    return val


def _regex_vin(text: str) -> str | None:
    for cand in _VIN_IN_TEXT.findall(text):
        if VIN_RE.match(cand):
            return cand.upper()
    return None


def parse_listing(text: str, use_llm: bool = True) -> ParsedListing:
    parsed = ParsedListing(
        vin=_regex_vin(text),
        mileage=_regex_mileage(text),
        price=int(m.group(1).replace(",", "")) if (m := _PRICE.search(text)) else None,
    )

    if not use_llm:
        return parsed

    system = (
        "You extract used-car listing facts. Return ONLY JSON with keys: "
        "vin (string|null), mileage (int|null), price (int|null), "
        "seller_claims (string[]), red_flags (string[]). red_flags are concerns a "
        "buyer should note: salvage/rebuilt title, prior accident, flood, dodged "
        "questions, 'as-is', odometer doubts, etc."
    )
    data = llm.complete_json(system, f"LISTING:\n{text[:4000]}")
    if not data:
        return parsed  # graceful fallback to regex-only

    parsed.used_llm = True
    parsed.vin = parsed.vin or (data.get("vin") or None)
    parsed.mileage = parsed.mileage or data.get("mileage")
    parsed.price = parsed.price or data.get("price")
    parsed.seller_claims = data.get("seller_claims") or []
    parsed.red_flags = data.get("red_flags") or []
    return parsed
