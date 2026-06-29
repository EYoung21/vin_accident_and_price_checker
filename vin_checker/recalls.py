"""Open safety recalls (and optionally complaints) via NHTSA's free API.

This is model-level data (by year/make/model), NOT this-VIN's crash history. It
answers "does this model have known defects?" — a useful buy/no-buy signal that
costs nothing and needs no key.

API: https://api.nhtsa.gov/
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from .config import CONFIG
from .decode import DecodedVin

RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
COMPLAINTS_URL = "https://api.nhtsa.gov/complaints/complaintsByVehicle"


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
        resp = requests.get(RECALLS_URL, params=params, timeout=CONFIG.http_timeout)
        resp.raise_for_status()
        results = resp.json().get("results") or []
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
            cresp = requests.get(
                COMPLAINTS_URL, params=params, timeout=CONFIG.http_timeout
            )
            cresp.raise_for_status()
            complaint_count = cresp.json().get("count")
        except (requests.RequestException, ValueError):
            complaint_count = None  # non-fatal

    return RecallReport(recalls=recalls, complaint_count=complaint_count)
