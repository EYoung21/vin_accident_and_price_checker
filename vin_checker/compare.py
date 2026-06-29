"""Compare cars from the backlog, then chat about the comparison.

`vincheck --compare` lists your logged cars numbered (best-to-worst); you pick a
few by number and it runs an LLM comparison on those, then drops into a follow-up
chat about the result (web-search enabled), like the single-car chat.
`vincheck --compare VIN VIN` skips the picker and compares those directly.
"""

from __future__ import annotations

import re
import sys

from . import llm
from .config import CONFIG
from .logstore import ranked


def _row_line(r: dict) -> str:
    med = f"${r['value_median']:,}" if r.get("value_median") else "?"
    offer = f"${r['offer']:,}" if r.get("offer") else "?"
    dist = f"{r['distance_mi']} mi" if r.get("distance_mi") else "?"
    return (f"{r.get('verdict','?')} {r.get('vehicle','?')} | {r.get('mileage','?')} mi | "
            f"offer/deal {offer} vs market {med} | {dist} from {CONFIG.home_name} | "
            f"{r.get('location','?')} | VIN {r.get('vin','')}")


def _compare_text(rows: list[dict], use_llm: bool) -> str:
    if not (use_llm and llm.available()):
        return "\n".join(f"  {i}. {_row_line(r)}" for i, r in enumerate(rows, 1))
    system = (
        f"Compare these used cars I'm considering, best to worst. Weigh value vs "
        f"market, condition/verdict (HARD PASS=avoid, UNVERIFIED=needs checking), the "
        f"offer/agreed price, and distance to me ({CONFIG.home_name} — closer is "
        f"better). Give a short ranked shortlist with a one-line reason each, then a "
        f"clear top pick and why. Plain terminal text — no markdown headers/tables/bold."
    )
    user = "CARS:\n" + "\n".join(_row_line(r) for r in rows)
    return llm.chat_text(system, [{"role": "user", "content": user}], max_tokens=900) \
        or "\n".join(f"  {i}. {_row_line(r)}" for i, r in enumerate(rows, 1))


def _chat_about(rows: list[dict], comparison: str, use_llm: bool) -> None:
    if not use_llm:
        return
    from . import chat
    briefing = ("You are my car-buying advisor comparing several used cars I'm "
                "considering. The cars and your prior comparison are below. Answer "
                "follow-ups using this plus web_search when helpful. Plain terminal "
                "text — no markdown headers/tables/bold.\n\n=== CARS ===\n"
                + "\n".join(_row_line(r) for r in rows)
                + "\n\n=== YOUR COMPARISON ===\n" + comparison)
    chat.converse(briefing, "\n💬 Ask about this comparison — tradeoffs, which to see "
                            "first, etc. It can search the web. Enter/'q' to quit.")


def _select(rows: list[dict]) -> list[dict]:
    """Show a numbered list and let the user pick which to compare."""
    print("\nCars you've checked (best → worst):")
    for i, r in enumerate(rows, 1):
        print(f"  {i}. {_row_line(r)}")
    try:
        sel = input("\nNumbers to compare (e.g. 1 3 5; Enter = all): ").strip()
    except (EOFError, KeyboardInterrupt):
        return []
    if not sel:
        return rows
    idx = [int(n) for n in re.findall(r"\d+", sel)]
    return [rows[i - 1] for i in idx if 1 <= i <= len(rows)]


def run(vins: list[str] | None = None, use_llm: bool = True) -> None:
    rows = ranked()
    if not rows:
        print("No cars checked yet — run `vincheck` on a few first.")
        return

    if vins:  # explicit VINs (full or partial), skip the picker
        want = [v.upper() for v in vins]
        rows = [r for r in rows if any(w in r.get("vin", "").upper() for w in want)]
    elif sys.stdin.isatty():
        rows = _select(rows)

    if len(rows) < 2:
        print("Pick at least 2 cars to compare.")
        return

    comparison = _compare_text(rows, use_llm)
    print("\n" + comparison)
    _chat_about(rows, comparison, use_llm)
