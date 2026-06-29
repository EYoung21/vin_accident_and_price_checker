"""Compare cars from the backlog — a colorful scorecard, then chat about it.

`vincheck --compare` lists your logged cars numbered (best-to-worst); you pick a
few by number and it renders a side-by-side comparison (value, distance, 0-60,
power, audio, bluetooth, reliability, safety, title), highlighting the best car in
each row, then an LLM ranked verdict + top pick, then a follow-up chat (web-search
enabled) like the single-car chat.
`vincheck --compare VIN VIN` skips the picker and compares those directly.
"""

from __future__ import annotations

import re
import shutil
import sys
import textwrap

from . import llm
from .config import CONFIG
from .logstore import ranked, save_check
from .report import BOLD, CYAN, DIM, GRN, RED, WHITE, YEL, paint

_CIRCLED = "①②③④⑤⑥⑦⑧⑨"

# What the buyer cares about (drives both the scorecard and the LLM weighting).
_PRIORITIES = ("value vs market", "0-60 / performance", "looks/condition",
               "bluetooth + audio/speakers", "reliability (known problems, recalls, "
               "complaints)", "distance to " + CONFIG.home_name, "title/safety")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _num(v):
    """Leading number out of an int/float or a messy string ('~8.5-9.2 s' -> 8.5)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def _research(r: dict) -> dict:
    return r.get("research") or {}


def _best(vals: list, lower_better: bool):
    """Index of the best numeric cell, or None if fewer than 2 are comparable."""
    nums = [(n, i) for i, v in enumerate(vals) if (n := _num(v)) is not None]
    if len(nums) < 2:
        return None
    return (min if lower_better else max)(nums)[1]


def _money(n) -> str:
    n = _num(n)
    return f"${int(n):,}" if n else "—"


def _vs_market(r: dict):
    """(display, numeric) — how far the offer/price sits under market median."""
    med, offer = _num(r.get("value_median")), _num(r.get("offer") or r.get("value_median"))
    if not (med and offer):
        return "—", None
    delta = int(med - offer)
    if delta > 0:
        return f"${delta:,} under", delta
    if delta < 0:
        return f"${-delta:,} over", delta
    return "at market", 0


def _verdict_mark(v: str) -> tuple[str, tuple]:
    v = v or ""
    if "HARD PASS" in v:
        return "❌ pass", (RED, BOLD)
    if "CAUTION" in v:
        return "⚠ caution", (YEL,)
    if "CLEAN" in v:
        return "✅ clean", (GRN,)
    return "❓ unverif", (DIM,)


# --------------------------------------------------------------------------- #
# back-fill: older log rows saved before research was stored
# --------------------------------------------------------------------------- #
def _enrich(rows: list[dict], use_llm: bool, progress=None) -> None:
    """Fill in whatever a logged row is missing (specs, recalls/safety, web research)
    so the scorecard isn't full of dashes. Incremental + cached: each piece is only
    fetched if absent, then persisted so the next compare is instant."""
    p = progress or (lambda *_: None)
    from .decode import decode_vin
    from .recalls import get_recalls, get_safety_ratings
    for r in rows:
        needs_research = use_llm and llm.available() and not _research(r)
        needs_safety = "recalls" not in r
        needs_specs = "hp" not in r
        if not (needs_research or needs_safety or needs_specs):
            continue
        try:
            d = decode_vin(r["vin"])
        except Exception:  # decode hiccup shouldn't kill the comparison
            continue
        if needs_specs:
            for k, v in (("hp", d.hp), ("doors", d.doors), ("drive_type", d.drive_type),
                         ("transmission", d.transmission), ("body_class", d.body_class)):
                r.setdefault(k, v)
        if needs_safety:
            try:
                rc, sf = get_recalls(d), get_safety_ratings(d)
                r["recalls"] = None if rc.error else rc.count
                r["complaints"] = rc.complaint_count
                r["ncap"] = sf.overall if sf else None
            except Exception:
                pass
        if needs_research:
            try:
                p(f"researching {r.get('vehicle') or r.get('vin')}")
                from .research import research_car
                res = research_car(d, r.get("mileage"), use_llm=True)
                if res.available:
                    r["research"] = {
                        "zero_to_sixty": res.zero_to_sixty, "performance": res.performance,
                        "audio": res.audio, "connectivity": res.connectivity,
                        "common_problems": res.common_problems,
                    }
            except Exception:
                pass
        save_check({k: v for k, v in r.items() if not k.startswith("_")})  # cache it


# --------------------------------------------------------------------------- #
# rendering — the colorful scorecard
# --------------------------------------------------------------------------- #
def _cell(raw, w: int, *codes, best: bool = False) -> str:
    raw = "—" if raw in (None, "") else str(raw)
    if len(raw) > w:
        raw = raw[: w - 1] + "…"
    pad = " " * (w - len(raw))
    return paint(raw, *((GRN, BOLD) if best else codes)) + pad


def _scorecard(rows: list[dict]) -> str:
    n = len(rows)
    term = shutil.get_terminal_size((100, 20)).columns
    label_w = 11
    cell_w = max(15, min(34, (term - label_w - 2 * n) // n))
    gap = "  "

    def line(label, cells, best_idx=None, codes=()):
        out = paint(label.ljust(label_w), DIM)
        out += gap.join(_cell(c, cell_w, *codes, best=(best_idx == i))
                        for i, c in enumerate(cells))
        return out

    L = ["", paint("🚗 COMPARISON", BOLD, CYAN) + paint("   (green = best in its row)", DIM)]

    # header: numbered car names
    names = [f"{_CIRCLED[i]} {r.get('vehicle') or r.get('vin','?')}" for i, r in enumerate(rows)]
    L.append(line("", names, codes=(BOLD, WHITE)))
    L.append(paint(" " * label_w + "─" * (n * cell_w + (n - 1) * len(gap)), DIM))

    # --- money / location ---
    offers = [r.get("offer") or r.get("value_median") for r in rows]
    L.append(line("price", [_money(o) for o in offers], _best(offers, True)))
    vm = [_vs_market(r) for r in rows]
    vm_best = _best([x for _, x in vm], False)
    vm_cells = []
    for i, (disp, delta) in enumerate(vm):
        codes = ((GRN, BOLD) if i == vm_best else (GRN,) if (delta or 0) > 0
                 else (RED,) if (delta or 0) < 0 else (DIM,))
        vm_cells.append(_cell(disp, cell_w, *codes))
    L.append(paint("vs market".ljust(label_w), DIM) + gap.join(vm_cells))
    L.append(line("market", [_money(r.get("value_median")) for r in rows]))
    dist = [r.get("distance_mi") for r in rows]
    L.append(line("distance", [f"{int(d)} mi" if _num(d) else "—" for d in dist], _best(dist, True)))

    # --- the car itself ---
    miles = [r.get("mileage") for r in rows]
    L.append(line("mileage", [f"{int(m):,}" if _num(m) else "—" for m in miles], _best(miles, True)))
    zsx = [_research(r).get("zero_to_sixty") for r in rows]
    L.append(line("0-60", zsx, _best(zsx, True)))   # lower is quicker
    hp = [r.get("hp") for r in rows]
    L.append(line("power", [f"{int(h)} hp" if _num(h) else "—" for h in hp], _best(hp, False)))
    L.append(line("drivetrain", [r.get("drive_type") for r in rows]))

    # --- reliability + safety ---
    probs = [len(_research(r).get("common_problems") or []) for r in rows]
    L.append(line("problems", [f"{c} known" if _research(r) else "—"
                               for c, r in zip(probs, rows)],
                  _best([(p if _research(r) else None) for p, r in zip(probs, rows)], True)))
    rec = [r.get("recalls") for r in rows]
    L.append(line("recalls", [str(int(x)) if _num(x) is not None else "—" for x in rec],
                  _best(rec, True)))
    comp = [r.get("complaints") for r in rows]
    L.append(line("complaints", [f"{int(x):,}" if _num(x) else "—" for x in comp], _best(comp, True)))
    ncap = [r.get("ncap") for r in rows]
    L.append(line("safety", [f"NCAP {int(x)}/5" if _num(x) else "—" for x in ncap], _best(ncap, False)))

    # --- title ---
    marks = [_verdict_mark(r.get("verdict")) for r in rows]
    out = paint("title".ljust(label_w), DIM)
    out += gap.join(_cell(t, cell_w, *c) for (t, c) in marks)
    L.append(out)
    return "\n".join(L)


def _details(rows: list[dict]) -> str:
    """The text-heavy stuff that doesn't fit a column: look, audio/speakers,
    bluetooth, and the reliability problem list."""
    L = ["", paint("🔎 THE DETAILS  (look · audio/speakers · bluetooth · reliability)", BOLD, CYAN)]
    for i, r in enumerate(rows):
        res = _research(r)
        L.append("")
        L.append(paint(f"{_CIRCLED[i]} {r.get('vehicle') or r.get('vin','?')}", BOLD, WHITE))

        def kv(label, val, color=CYAN):
            if not val:
                return
            wrapped = textwrap.wrap(str(val), 78)
            L.append("   " + paint(label + ": ", color) + wrapped[0])
            for line in wrapped[1:]:
                L.append("      " + line)

        kv("look/drive", res.get("performance"))
        kv("audio/speakers", res.get("audio"))
        kv("bluetooth", res.get("connectivity"))
        cps = res.get("common_problems") or []
        if cps:
            L.append("   " + paint("reliability — known problems:", YEL))
            for x in cps[:5]:
                for j, line in enumerate(textwrap.wrap(str(x), 72)):
                    L.append("      " + (paint("- ", YEL, BOLD) if j == 0 else "  ") + line)
        elif not res:
            L.append("   " + paint("(no research on file — run this car in vincheck for "
                                   "0-60/audio/reliability)", DIM))
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# LLM verdict + chat
# --------------------------------------------------------------------------- #
def _row_line(r: dict) -> str:
    res = _research(r)
    bits = [
        r.get("verdict", "?"), r.get("vehicle", "?"),
        f"{r.get('mileage','?')} mi" if r.get("mileage") else None,
        f"offer/deal {_money(r.get('offer'))} vs market {_money(r.get('value_median'))}",
        f"{r['distance_mi']} mi from {CONFIG.home_name}" if r.get("distance_mi") else None,
        f"0-60 {res.get('zero_to_sixty')}" if res.get("zero_to_sixty") else None,
        f"{r.get('hp')}hp" if r.get("hp") else None,
        r.get("drive_type"),
        f"audio: {res.get('audio')}" if res.get("audio") else None,
        f"bluetooth: {res.get('connectivity')}" if res.get("connectivity") else None,
        f"NCAP {r.get('ncap')}/5" if r.get("ncap") else None,
        f"{r.get('recalls')} recalls" if r.get("recalls") is not None else None,
        f"{r.get('complaints')} complaints" if r.get("complaints") else None,
        ("known problems: " + "; ".join(res.get("common_problems", [])[:4]))
        if res.get("common_problems") else None,
        r.get("location"), f"VIN {r.get('vin','')}",
    ]
    return " | ".join(b for b in bits if b)


def _compare_text(rows: list[dict], use_llm: bool) -> str:
    fallback = "\n".join(f"  {i}. {_row_line(r)}" for i, r in enumerate(rows, 1))
    if not (use_llm and llm.available()):
        return fallback
    system = (
        "Compare these used cars I'm considering and rank them best to worst FOR ME. "
        "Weigh what I care about, roughly in this order: " + "; ".join(_PRIORITIES) + ". "
        "Treat verdict HARD PASS as avoid and UNVERIFIED as needs-checking. Give a short "
        "ranked shortlist with a one-line reason each (cite the specifics: price vs "
        "market, 0-60, reliability/known problems, audio/bluetooth, distance), then a "
        "clear TOP PICK and why, and one thing to verify before buying it. Plain "
        "terminal text — no markdown headers, tables, or bold."
    )
    user = "CARS:\n" + "\n".join(_row_line(r) for r in rows)
    return llm.chat_text(system, [{"role": "user", "content": user}], max_tokens=1000) or fallback


def _chat_about(rows: list[dict], comparison: str, use_llm: bool) -> None:
    if not use_llm:
        return
    from . import chat
    briefing = (
        "You are my car-buying advisor comparing several used cars I'm considering. I "
        "care about (roughly in order): " + "; ".join(_PRIORITIES) + ". The cars and "
        "your prior comparison are below. Answer follow-ups using this plus web_search "
        "when helpful. Plain terminal text — no markdown headers/tables/bold.\n\n"
        "=== CARS ===\n" + "\n".join(_row_line(r) for r in rows)
        + "\n\n=== YOUR COMPARISON ===\n" + comparison)
    chat.converse(briefing, "\n💬 Ask about this comparison — tradeoffs, which to see "
                            "first, reliability, etc. It can search the web. Enter/'q' to quit.")


# --------------------------------------------------------------------------- #
# picker + orchestration
# --------------------------------------------------------------------------- #
def _select(rows: list[dict]) -> list[dict]:
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

    def prog(msg):
        print(f"  … {msg}", file=sys.stderr, flush=True)

    _enrich(rows, use_llm, progress=prog)        # back-fill 0-60/audio/reliability if missing
    print(_scorecard(rows))                       # colored at-a-glance grid
    print(_details(rows))                         # look / audio / bluetooth / reliability
    comparison = _compare_text(rows, use_llm)     # ranked LLM verdict + top pick
    print("\n" + paint("🏆 RANKING", BOLD, CYAN) + "\n" + comparison)
    _chat_about(rows, comparison, use_llm)        # follow-up Q&A about the comparison
