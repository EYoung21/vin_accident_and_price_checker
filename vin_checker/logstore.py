"""Append-only log of cars you've checked, plus a ranked comparison.

Lives in automation_html/ (git-ignored → only ever in your PRIVATE repo) because
it's your personal shopping history. `vincheck --list` ranks saved cars so you
know which to go see first: clean titles first, then best deal vs. market.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent.parent / "automation_html" / "checks_log.jsonl"

# Lower = better. Drives the "which to see first" ordering.
_VERDICT_RANK = {"✅ LOOKS CLEAN": 0, "⚠️  CAUTION": 1, "❌ HARD PASS": 2}


def save_check(rec: dict) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), **rec}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # logging is best-effort


def load_checks() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    rows = [json.loads(line) for line in LOG_PATH.read_text().splitlines() if line.strip()]
    # keep only the most recent check per VIN
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r.get("vin", "")] = r
    return list(latest.values())


def _deal(rec: dict) -> int | None:
    med, offer = rec.get("value_median"), rec.get("offer")
    return (med - offer) if (med and offer) else None


def render_log() -> str:
    rows = load_checks()
    if not rows:
        return "No cars checked yet. Run `vincheck` on a car first."

    def sort_key(r):
        return (_VERDICT_RANK.get(r.get("verdict", ""), 1), -((_deal(r) or -10**9)))

    rows.sort(key=sort_key)
    out = ["", "CARS YOU'VE CHECKED  (best to see first → worst)", "=" * 64]
    for r in rows:
        med = f"${r['value_median']:,}" if r.get("value_median") else "n/a"
        offer = f"${r['offer']:,}" if r.get("offer") else "n/a"
        delta = _deal(r)
        deal = f" ({'+' if delta and delta > 0 else ''}{delta:,} vs mkt)" if delta else ""
        out.append(f"{r.get('verdict', '?'):<14} {r.get('vehicle', r.get('vin', '')):<34}")
        out.append(f"   offer {offer}{deal}  ·  market {med}  ·  {r.get('vin', '')}")
    return "\n".join(out)
