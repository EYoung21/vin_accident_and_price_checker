"""LLM comparison across the backlog of cars you've run."""

from __future__ import annotations

from . import llm
from .config import CONFIG
from .logstore import load_checks, render_log


def compare_cars(vins: list[str] | None = None, use_llm: bool = True) -> str:
    rows = load_checks()
    if vins:  # narrow to specific cars (full or partial VIN, case-insensitive)
        want = [v.upper() for v in vins]
        rows = [r for r in rows
                if any(w in r.get("vin", "").upper() for w in want)]
        if not rows:
            return "No logged cars match those VINs. Run them first, or check --list."
    if not rows:
        return "No cars checked yet — run `vincheck` on a few first."
    if not (use_llm and llm.available()):
        return render_log()  # deterministic ranking fallback

    lines = []
    for r in rows:
        lines.append(
            f"- {r.get('vehicle','?')} | {r.get('mileage','?')} mi | verdict "
            f"{r.get('verdict','?')} | market ${r.get('value_median','?')} | "
            f"offer/deal ${r.get('offer','?')} | {r.get('location','?')} "
            f"~{r.get('distance_mi','?')} mi from {CONFIG.home_name} | VIN {r.get('vin','')}"
        )
    system = (
        f"Compare these used cars I'm considering and recommend which to pursue, best "
        f"to worst. Weigh: value vs market, condition/verdict (treat HARD PASS as "
        f"avoid, UNVERIFIED as needs-checking), the offer/agreed price, and distance "
        f"to me ({CONFIG.home_name} — closer is better). Give a short ranked shortlist "
        f"with a one-line reason each, then a clear top pick and why. Plain text for a "
        f"terminal — no markdown headers, tables, or bold."
    )
    return llm.chat_text(system, [{"role": "user", "content": "CARS:\n" + "\n".join(lines)}],
                         max_tokens=900) or render_log()
