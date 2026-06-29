"""Open safety recalls (and optionally complaints) via NHTSA's free API.

This is model-level data (by year/make/model), NOT this-VIN's crash history. It
answers "does this model have known defects?" — a useful buy/no-buy signal that
costs nothing and needs no key.

API: https://api.nhtsa.gov/
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from . import http_cache
from .decode import DecodedVin

RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"
SAFETY_MMY_URL = "https://api.nhtsa.gov/SafetyRatings/modelyear/{year}/make/{make}/model/{model}"
SAFETY_ID_URL = "https://api.nhtsa.gov/SafetyRatings/VehicleId/{vid}"


@dataclass
class Recall:
    campaign: str
    component: str
    summary: str
    remedy: str


@dataclass
class RecallReport:
    recalls: list[Recall]
    complaint_count: int | None = None
    error: str | None = None

    @property
    def count(self) -> int:
        return len(self.recalls)


def _params(decoded: DecodedVin) -> dict | None:
    if not (decoded.make and decoded.model and decoded.year):
        return None
    return {"make": decoded.make, "model": decoded.model, "modelYear": decoded.year}


def get_recalls(decoded: DecodedVin, include_complaints: bool = True) -> RecallReport:
    params = _params(decoded)
    if params is None:
        return RecallReport(recalls=[], error="insufficient decode data for recalls")

    try:
        results = http_cache.get_json(RECALLS_URL, params=params, ttl=604800).get("results") or []
    except (requests.RequestException, ValueError) as e:
        return RecallReport(recalls=[], error=f"recall lookup failed: {e}")

    recalls = [
        Recall(
            campaign=r.get("NHTSACampaignNumber", "").strip(),
            component=r.get("Component", "").strip(),
            summary=r.get("Summary", "").strip(),
            remedy=r.get("Remedy", "").strip(),
        )
        for r in results
    ]

    complaint_count = None
    if include_complaints:
        try:
            complaint_count = http_cache.get_json(
                COMPLAINTS_URL, params=params, ttl=604800).get("count")
        except (requests.RequestException, ValueError):
            complaint_count = None  # non-fatal

    return RecallReport(recalls=recalls, complaint_count=complaint_count)


@dataclass
class SafetyRatings:
    overall: str | None = None     # NCAP overall stars, e.g. "5"
    rollover: str | None = None
    error: str | None = None


def get_safety_ratings(decoded: DecodedVin) -> SafetyRatings:
    """NHTSA NCAP crash-test stars (free, no key). Two calls: model-year lookup
    → VehicleId → ratings. Takes the first matching variant."""
    if not (decoded.make and decoded.model and decoded.year):
        return SafetyRatings(error="insufficient decode data")
    try:
        url = SAFETY_MMY_URL.format(year=decoded.year, make=decoded.make, model=decoded.model)
        results = http_cache.get_json(url, ttl=2592000).get("Results") or []
        if not results:
            return SafetyRatings(error="no NCAP rating for this model")
        vid = results[0].get("VehicleId")
        rating = (http_cache.get_json(SAFETY_ID_URL.format(vid=vid), ttl=2592000)
                  .get("Results") or [{}])[0]
        overall = rating.get("OverallRating")
        return SafetyRatings(
            overall=overall if overall and overall != "Not Rated" else None,
            rollover=rating.get("RolloverRating") or None,
        )
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        return SafetyRatings(error=f"safety lookup failed: {e}")
