"""Post-synthesis follow-up chat.

After the report is generated, drop into a Q&A with the model already briefed on
everything (specs, history, web research, pros/cons, the offer, and your pasted
chat). It can call a web_search tool when a question needs fresh/external info
(Serper if SERPER_API_KEY is set, else free DuckDuckGo).
"""

from __future__ import annotations

import re
import shutil
import sys
import textwrap

from . import llm, websearch
from .report import BOLD, DIM, paint

_SYSTEM_PREFIX = (
    "You are my used-car buying advisor. Everything currently known about the car "
    "I'm considering is below. Answer my follow-up questions concisely and honestly, "
    "using this info plus your knowledge; call the web_search tool when fresh or "
    "external info would help (current prices, specific specs, known problems, "
    "recalls). For negotiation questions, use the deal state.\n"
    "STYLE: write for a plain terminal — short paragraphs and simple '- ' bullets "
    "only. No markdown headers (#), no bold (**), no tables, no '---' rules. Be "
    "direct; answer first, then briefly why. Don't ask clarifying questions unless "
    "truly necessary — make a reasonable assumption and answer.\n\n"
    "=== WHAT WE KNOW ===\n"
)


def _fmt(text: str) -> str:
    """Clean stray markdown, wrap, and indent for readable terminal output."""
    width = min(88, max(60, shutil.get_terminal_size((100, 20)).columns - 4))
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            out.append("")
            continue
        if len(line.strip()) >= 3 and set(line.strip()) <= set("-*_"):  # md rule
            out.append("   " + paint("─" * (width - 6), DIM))
            continue
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)   # drop bold markers
        line = re.sub(r"`([^`]+)`", r"\1", line)        # drop code ticks
        if (m := re.match(r"^\s*#{1,6}\s+(.*)$", line)):  # header -> bold line
            for w in textwrap.wrap(m.group(1), width):
                out.append("   " + paint(w, BOLD))
            continue
        if (m := re.match(r"^\s*[-*]\s+(.*)$", line)):    # bullet
            out += textwrap.wrap(m.group(1), width, initial_indent="   - ",
                                 subsequent_indent="     ")
            continue
        if (m := re.match(r"^\s*(\d+)\.\s+(.*)$", line)):  # numbered
            out += textwrap.wrap(m.group(2), width, initial_indent=f"   {m.group(1)}. ",
                                 subsequent_indent="      ")
            continue
        out += textwrap.wrap(line, width, initial_indent="   ", subsequent_indent="   ")
    return "\n".join(out)


def _briefing(report, research, neg, pros, cons, context) -> str:
    d, c, h, rc = report.decoded, report.comps, report.history, report.recalls
    L = [f"VEHICLE: {d.full_name} | {d.body_class or ''} | {d.hp or '?'} hp | "
         f"{d.engine or ''} | {d.drive_type or ''} | {d.transmission or ''}"]
    if report.mileage:
        L.append(f"MILEAGE: {report.mileage:,}")
    if c.count and c.median:
        L.append(f"VALUE (comps): low ${c.low:,} / median ${c.median:,} / high ${c.high:,} ({c.count})")
    L.append(f"HISTORY: title {h.title_status}, salvage-auction {h.auction_status}"
             + (f", brands {','.join(h.title_brands)}" if h.title_brands else ""))
    if h.auction_details:
        L.append("AUCTION RECORD: " + "; ".join(h.auction_details))
    if not rc.error:
        ncap = f", NCAP {report.safety.overall}/5" if (report.safety and report.safety.overall) else ""
        L.append(f"SAFETY: {rc.count} recalls, {rc.complaint_count or 0} complaints{ncap}")
    if research and research.available:
        L.append(f"0-60: {research.zero_to_sixty} | audio: {research.audio} | bluetooth: {research.connectivity}")
        if research.common_problems:
            L.append("COMMON PROBLEMS: " + "; ".join(research.common_problems))
        if research.inspect_in_person:
            L.append("INSPECT IN PERSON: " + "; ".join(research.inspect_in_person))
    if pros:
        L.append("PROS: " + "; ".join(pros))
    if cons:
        L.append("CONS: " + "; ".join(cons))
    if neg and neg.final_offer:
        L.append(f"RECOMMENDED OFFER: ${neg.final_offer:,}"
                 + (f" — {neg.current_state}" if neg.current_state else ""))
    if context:
        L.append("\nORIGINAL LISTING / SELLER CHAT:\n" + context[:8000])
    return "\n".join(L)


def chat_loop(report, research=None, neg=None, pros=None, cons=None, context="") -> None:
    if not (sys.stdin.isatty() and llm.available()):
        return
    system = _SYSTEM_PREFIX + _briefing(report, research, neg, pros or [], cons or [], context)
    from .promptio import read_block
    print("\n💬 Chat about this car — specs, problems, negotiation. It can search the "
          "web. Press Enter on a blank line (or 'q') to quit.")
    messages: list[dict] = []
    while True:
        print()
        q = read_block(ps="you> ", show_hint=False).strip()  # paste-aware: a pasted
        if not q or q.lower() in ("q", "quit", "exit"):       # thread = ONE message
            break
        messages.append({"role": "user", "content": q})
        print(paint("  … thinking", DIM), file=sys.stderr, flush=True)
        try:
            ans = (llm.chat_with_search(system, messages, websearch.search)
                   or llm.chat_text(system, messages))
        except KeyboardInterrupt:
            print("\n(stopped)")
            break
        if not ans:
            print("  (no response — try again)")
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": ans})
        print(paint("  " + "─" * 58, DIM))
        print(_fmt(ans))
