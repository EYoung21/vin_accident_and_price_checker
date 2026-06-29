"""Distance from a car's location to home (Signal Mountain, TN 37377).

Free + no key: extract the seller's city via the LLM, geocode it with OpenStreetMap
Nominatim, and get driving distance/time from the OSRM demo server (straight-line
haversine as fallback). All cached.
"""

from __future__ import annotations

import math

from . import http_cache, llm
from .config import CONFIG

HOME = (CONFIG.home_lat, CONFIG.home_lon)   # from config.toml (default Signal Mtn)
HOME_NAME = CONFIG.home_name
_UA = {"User-Agent": "vin-checker/1.0 (personal car shopping)"}


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    R = 3958.8  # miles
    (lat1, lon1), (lat2, lon2) = map(lambda p: (math.radians(p[0]), math.radians(p[1])), (a, b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def geocode(place: str) -> tuple[float, float] | None:
    try:
        data = http_cache.get_json(
            "https://nominatim.openstreetmap.org/search",
            params={"q": place, "format": "json", "limit": 1, "countrycodes": "us"},
            headers=_UA, ttl=2592000)
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None


def _osrm(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}"
        data = http_cache.get_json(url, params={"overview": "false"}, ttl=2592000)
        r = (data.get("routes") or [None])[0]
        if r:
            return r["distance"] / 1609.34, r["duration"] / 60.0
    except Exception:
        pass
    return None


def distance(place: str) -> dict | None:
    """Returns {place, straight_mi, drive_mi?, drive_min?} or None if not locatable."""
    coords = geocode(place)
    if not coords:
        return None
    out = {"place": place, "straight_mi": round(_haversine(coords, HOME))}
    if (d := _osrm(coords, HOME)):
        out["drive_mi"], out["drive_min"] = round(d[0]), round(d[1])
    return out


def extract_location(context: str, use_llm: bool = True) -> str | None:
    """Pull the SELLER's city/location for THIS car out of the pasted text."""
    if not (use_llm and llm.available()) or not context:
        return None
    system = ("Extract the SELLER's location (city, state) for the car being sold. "
              "Ignore unrelated marketplace listings/other cars. "
              'Return ONLY JSON: {"location": "City, ST"} or {"location": null}.')
    data = llm.complete_json(system, context[:8000])
    loc = (data or {}).get("location")
    return loc if loc and isinstance(loc, str) else None
