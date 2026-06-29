"""Backlog of cars you've run.

Logs to a top-level, easy-to-find folder (default `car_log/`, set in config.toml):
  - log.jsonl : structured, one line per check (source of truth for compare/list)
  - cars.md   : human-readable table you can open and skim

It's git-ignored from the public repo and synced to your private one.
"""

from __future__ import annotations

import json
from datetime import datetime

from .config import CONFIG

_VERDICT_RANK = {"✅ LOOKS CLEAN": 0, "❓ UNVERIFIED": 1, "⚠️ CAUTION": 2, "❌ HARD PASS": 3}


def _dir():
    d = CONFIG.log_path
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_file():
    return _dir() / "log.jsonl"


def save_check(rec: dict) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
    try:
        with _log_file().open("a") as f:
            f.write(json.dumps(rec) + "\n")
        _write_markdown()
    except OSError:
        pass


def load_checks() -> list[dict]:
    path = _log_file()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    latest: dict[str, dict] = {}  # most recent check per VIN
    for r in rows:
        latest[r.get("vin", "")] = r
    return list(latest.values())


def _deal(rec: dict) -> int | None:
    med, offer = rec.get("value_median"), rec.get("offer")
    return (med - offer) if (med and offer) else None


def _sorted() -> list[dict]:
    rows = load_checks()
    rows.sort(key=lambda r: (_VERDICT_RANK.get(r.get("verdict", ""), 1),
                             -((_deal(r) or -10**9)), r.get("distance_mi") or 10**9))
    return rows


def ranked() -> list[dict]:
    """Public: logged cars, best-to-worst (verdict, then deal, then distance)."""
    return _sorted()


def _write_markdown() -> None:
    rows = _sorted()
    lines = ["# Cars checked", "",
             "| Verdict | Vehicle | Miles | Market | Offer/Deal | Distance | Location | VIN | When |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        med = f"${r['value_median']:,}" if r.get("value_median") else "—"
        offer = f"${r['offer']:,}" if r.get("offer") else "—"
        dist = f"{r['distance_mi']} mi" if r.get("distance_mi") else "—"
        miles = f"{r['mileage']:,}" if r.get("mileage") else "—"
        lines.append(f"| {r.get('verdict','?')} | {r.get('vehicle','')} | {miles} | {med} "
                     f"| {offer} | {dist} | {r.get('location','—') or '—'} | {r.get('vin','')} "
                     f"| {r.get('ts','')[:10]} |")
    try:
        (_dir() / "cars.md").write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def render_log() -> str:
    rows = _sorted()
    if not rows:
        return "No cars checked yet. Run `vincheck` on a car first."
    out = ["", "CARS YOU'VE CHECKED  (best to see first → worst)", "=" * 64]
    for r in rows:
        med = f"${r['value_median']:,}" if r.get("value_median") else "n/a"
        offer = f"${r['offer']:,}" if r.get("offer") else "n/a"
        delta = _deal(r)
        deal = f" ({'+' if delta and delta > 0 else ''}{delta:,} vs mkt)" if delta else ""
        dist = f"  ·  {r['distance_mi']} mi away" if r.get("distance_mi") else ""
        out.append(f"{r.get('verdict', '?'):<14} {r.get('vehicle', r.get('vin', '')):<34}")
        out.append(f"   offer {offer}{deal}  ·  market {med}{dist}"
                   + (f"  ·  {r['location']}" if r.get("location") else ""))
    out.append(f"\nfull log: {_dir()/'cars.md'}")
    return "\n".join(out)
