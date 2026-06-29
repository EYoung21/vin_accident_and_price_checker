"""VIN decode via NHTSA vPIC (free, no key, no captcha).

vPIC is the free government spine of the whole tool: it turns a raw VIN into
structured year/make/model/trim that every downstream step (comps, recalls,
history) needs as input.

API: https://vpic.nhtsa.dot.gov/api/
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from . import http_cache

VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

# A VIN is 17 chars, no I/O/Q to avoid confusion with 1/0.
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)


class DecodeError(RuntimeError):
    pass


def is_valid_vin(vin: str) -> bool:
    return bool(VIN_RE.match(vin.strip()))


@dataclass
class DecodedVin:
    vin: str
    year: str | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None
    body_class: str | None = None
    engine: str | None = None
    drive_type: str | None = None
    fuel: str | None = None
    hp: str | None = None
    doors: str | None = None
    transmission: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def ymm(self) -> str:
        return " ".join(p for p in (self.year, self.make, self.model) if p)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.year, self.make, self.model, self.trim) if p)


def _engine_summary(r: dict) -> str | None:
    disp = r.get("DisplacementL")
    cyl = r.get("EngineCylinders")
    fuel = r.get("FuelTypePrimary")
    parts = []
    if disp:
        try:
            parts.append(f"{float(disp):.1f}L")
        except ValueError:
            parts.append(f"{disp}L")
    if cyl:
        parts.append(f"{cyl}cyl")
    if fuel:
        parts.append(fuel)
    return " ".join(parts) or None


def decode_vin(vin: str) -> DecodedVin:
    """Decode a VIN to factory specs. Raises DecodeError on invalid/empty result."""
    vin = vin.strip().upper()
    if not is_valid_vin(vin):
        raise DecodeError(f"'{vin}' is not a valid 17-character VIN")

    try:
        results = http_cache.get_json(VPIC_URL.format(vin=vin), ttl=2592000).get("Results") or []
    except (requests.RequestException, ValueError) as e:
        raise DecodeError(f"vPIC request failed: {e}") from e

    if not results:
        raise DecodeError("vPIC returned no results")

    r = results[0]
    # vPIC reports problems in ErrorCode ("0" == clean). Non-zero may still
    # carry partial data, so we surface it but don't hard-fail.
    if not r.get("Make") and not r.get("ModelYear"):
        raise DecodeError(
            f"vPIC could not decode this VIN (ErrorText: {r.get('ErrorText')})"
        )

    return DecodedVin(
        vin=vin,
        year=r.get("ModelYear") or None,
        make=(r.get("Make") or "").title() or None,
        model=r.get("Model") or None,
        trim=r.get("Trim") or None,
        body_class=r.get("BodyClass") or None,
        engine=_engine_summary(r),
        drive_type=r.get("DriveType") or None,
        fuel=r.get("FuelTypePrimary") or None,
        hp=(str(int(float(hp))) if (hp := r.get("EngineHP")) else None),
        doors=r.get("Doors") or None,
        transmission=r.get("TransmissionStyle") or None,
        raw=r,
    )
