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


def converse(system: str, intro: str) -> list[dict]:
    """Reusable chat loop (web-search enabled, paste-aware, formatted). Interactive
    only. Returns the conversation (so callers can summarize/persist it). Used for the
    single-car follow-up, the load-from-log chat, and the comparison follow-up."""
    if not (sys.stdin.isatty() and llm.available()):
        return []
    from .promptio import read_block
    print(intro)
    messages: list[dict] = []
    while True:
        print()
        q = read_block(ps="you> ", show_hint=False).strip()  # paste-aware: a pasted
        if not q or q.lower() in ("q", "quit", "exit"):       # block = ONE message
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
    return messages


def chat_loop(report, research=None, neg=None, pros=None, cons=None, context="",
              prior_notes: str = "") -> list[dict]:
    system = _SYSTEM_PREFIX + _briefing(report, research, neg, pros or [], cons or [], context)
    if prior_notes:
        system += "\n\nPRIOR DISCUSSION NOTES (from earlier chats about this car):\n" + prior_notes
    return converse(system, "\n💬 Chat about this car — specs, problems, negotiation. It "
                            "can search the web. Press Enter on a blank line (or 'q') to quit.")


def summarize_chat(messages: list[dict], use_llm: bool = True) -> str | None:
    """Condense a finished chat into a few bullet lines to stash in the car's log, so a
    later `--chat` can pick the conversation back up."""
    if not messages or not (use_llm and llm.available()):
        return None
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    system = ("Summarize this buyer's chat about a specific used car into 2-4 short "
              "bullet lines capturing what was asked/decided (concerns raised, advice "
              "given, negotiation/plan, next steps). Terse, factual, no preamble. "
              "Start each line with '- '.")
    return llm.chat_text(system, [{"role": "user", "content": convo[:8000]}], max_tokens=300) or None


def _briefing_from_log(rec: dict) -> str:
    """Rebuild a single-car briefing from a stored log record (no re-fetch)."""
    res = rec.get("research") or {}
    L = [f"VEHICLE: {rec.get('vehicle','?')}  (VIN {rec.get('vin','')})"]
    if rec.get("mileage"):
        L.append(f"MILEAGE: {rec['mileage']:,}")
    specs = "  ·  ".join(s for s in (
        rec.get("body_class"), f"{rec['doors']}dr" if rec.get("doors") else "",
        f"{rec['hp']} hp" if rec.get("hp") else "", rec.get("drive_type"),
        rec.get("transmission")) if s)
    if specs:
        L.append("SPECS: " + specs)
    if rec.get("value_median"):
        L.append(f"VALUE (comps): low ${rec.get('value_low') or '?'} / median "
                 f"${rec['value_median']:,} / high ${rec.get('value_high') or '?'}")
    if rec.get("offer"):
        tag = "DEAL AGREED" if rec.get("deal_agreed") else "recommended offer"
        L.append(f"{tag}: ${rec['offer']:,}")
    L.append(f"VERDICT: {rec.get('verdict','?')}")
    if rec.get("ncap") or rec.get("recalls") is not None:
        L.append(f"SAFETY: {rec.get('recalls','?')} recalls, {rec.get('complaints',0) or 0} "
                 f"complaints" + (f", NCAP {rec['ncap']}/5" if rec.get("ncap") else ""))
    if res.get("zero_to_sixty") or res.get("audio"):
        L.append(f"0-60: {res.get('zero_to_sixty','?')} | drive: {res.get('performance','?')}")
        L.append(f"audio: {res.get('audio','?')} | bluetooth: {res.get('connectivity','?')}")
    if res.get("common_problems"):
        L.append("COMMON PROBLEMS: " + "; ".join(res["common_problems"]))
    if rec.get("location"):
        L.append(f"LOCATION: {rec['location']}"
                 + (f" (~{rec['distance_mi']} mi away)" if rec.get("distance_mi") else ""))
    return "\n".join(L)


def _pick_car(rows: list[dict]) -> dict | None:
    print("\nWhich car do you want to talk about?")
    for i, r in enumerate(rows, 1):
        dist = f" · {r['distance_mi']} mi" if r.get("distance_mi") else ""
        offer = f" · offer ${r['offer']:,}" if r.get("offer") else ""
        print(f"  {i}. {r.get('verdict','?')} {r.get('vehicle','?')}{offer}{dist}"
              f"  ({r.get('location','?') or '?'})")
    try:
        sel = input("\nPick a number: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    nums = re.findall(r"\d+", sel)
    if not nums:
        return None
    i = int(nums[0])
    return rows[i - 1] if 1 <= i <= len(rows) else None


def chat_from_log(vin: str | None = None, use_llm: bool = True) -> None:
    """Load a previously-checked car from the log and chat about it — no re-pasting.
    No VIN given → pick from a numbered list. Persists a short summary on exit."""
    from .logstore import ranked, save_check
    rows = ranked()
    if not rows:
        print("No cars logged yet — run `vincheck` on a car first.")
        return

    rec = None
    if vin:
        want = vin.upper()
        matches = [r for r in rows if want in r.get("vin", "").upper()]
        if not matches:
            print(f"No logged car matches '{vin}'.")
        else:
            rec = matches[0]
    if rec is None:
        rec = _pick_car(rows) if sys.stdin.isatty() else None
    if rec is None:
        return

    prior = rec.get("chat_summary") or ""
    system = (_SYSTEM_PREFIX + _briefing_from_log(rec)
              + ("\n\nPRIOR DISCUSSION NOTES (earlier chats about this car):\n" + prior
                 if prior else ""))
    convo = converse(system, f"\n💬 Chatting about your {rec.get('vehicle','car')}. Ask "
                             "anything — specs, problems, negotiation. It can search the "
                             "web. Enter on a blank line (or 'q') to quit.")

    # Persist a rolling summary so the next --chat continues where this left off.
    summary = summarize_chat(convo, use_llm)
    if summary:
        from datetime import date
        entry = f"[{date.today().isoformat()}]\n{summary}"
        combined = ((prior + "\n" + entry) if prior else entry).strip()[-2000:]
        save_check({**{k: v for k, v in rec.items() if not k.startswith("_")},
                    "chat_summary": combined})
        print(paint("\n  (saved a summary to this car's log)", DIM))
