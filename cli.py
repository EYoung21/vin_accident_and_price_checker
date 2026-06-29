#!/usr/bin/env python3
"""VIN accident & price checker — CLI.

Run with no arguments for the interactive tool (prompts for VIN, mileage, and a
pasted listing/seller chat, then prints a screenshot-able card with an offer):

    python cli.py

Or drive it with flags:

    python cli.py --vin 1HGCR2F3XFA027534 --mileage 95000
    python cli.py --vin 1HGCR2F3XFA027534 --context listing.txt
    python cli.py --listing listing.txt --json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vin_checker.decode import DecodeError, is_valid_vin
from vin_checker.report import (
    build_report,
    render_card,
    render_json,
    render_diagnostics,
    render_draft,
    render_negotiation,
    render_offer_private,
    render_proscons,
    render_research,
    render_text,
    set_color,
    verdict,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Free VIN history + private-party value + offer")
    p.add_argument("--vin", help="17-character VIN (omit all flags for interactive mode)")
    p.add_argument("--listing", type=Path, help="text file with a pasted listing")
    p.add_argument("--mileage", type=int, help="odometer, tightens the comps")
    p.add_argument("--context", type=Path, help="text file: listing + seller chat → offer")
    p.add_argument("--list", action="store_true", dest="list_checks",
                   help="show all cars you've checked, ranked best-to-worst")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a card")
    p.add_argument("--plain", action="store_true", help="plain text instead of the card")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    p.add_argument("--no-llm", action="store_true", help="disable LLM parsing + offer")
    p.add_argument("--debug", action="store_true",
                   help="print diagnostics (comp filtering, why history is unverified, errors)")
    p.add_argument("--statvin-fixture", type=Path, help="captured stat.vin result HTML")
    p.add_argument("--vincheck-fixture", type=Path, help="captured vincheck.info HTML")
    return p.parse_args(argv)


def _read_block_basic(prompt: str) -> str:
    """Fallback reader (piped input, or no prompt_toolkit): blank line / Ctrl-D ends."""
    print(prompt)
    print("(paste it all, then press Enter on an empty line to finish)")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:  # Ctrl-D also finishes
            break
        if line.strip() == "":
            if lines:  # a blank line after some content = done
                break
            continue   # ignore blank lines before any content
        lines.append(line)
    return "\n".join(lines).strip()


def _read_block(prompt: str) -> str:
    """Claude-Code-style paste: bracketed paste keeps a multi-line block intact
    (internal blank lines don't submit early); a single Enter sends it."""
    if not sys.stdin.isatty():
        return _read_block_basic(prompt)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
    except ImportError:
        return _read_block_basic(prompt)

    print(prompt)
    print("(paste freely — big pastes collapse to a placeholder; Enter sends, "
          "Option+Enter / Shift+Enter = new line)")
    kb = KeyBindings()
    pastes: dict[str, str] = {}  # placeholder token -> real pasted text

    @kb.add("enter")          # Enter (\r) submits
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("c-j")              # Shift+Enter (mappable terminals) / \n
    @kb.add("escape", "enter")  # Option/Esc+Enter (works everywhere)
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add(Keys.BracketedPaste)  # collapse multi-line pastes, Claude-Code-style
    def _(event):
        # Pasted text may use \r or \r\n as line separators — normalize so the line
        # count is right and the stored text has real newlines.
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in data and len(data) <= 80:
            event.current_buffer.insert_text(data)  # small paste → inline
            return
        token = f"[Pasted text #{len(pastes) + 1} +{data.count(chr(10)) + 1} lines]"
        pastes[token] = data
        event.current_buffer.insert_text(token)

    try:
        text = PromptSession(multiline=True, key_bindings=kb).prompt("> ")
    except (EOFError, KeyboardInterrupt):
        return ""
    for token, data in pastes.items():  # expand placeholders back to real text
        text = text.replace(token, data)
    return text.strip()


def _interactive() -> tuple[str, int | None, str]:
    print("=== VIN checker — paste a car you're looking at ===\n")
    # Paste FIRST (this is the collapse-aware block), then pull VIN + mileage out of
    # it. This way there's one place to paste and it always behaves like Claude Code.
    context = _read_block(
        "Paste everything — the listing + your chat with the seller (or just a VIN):")

    from vin_checker.listing_parse import parse_listing
    parsed = parse_listing(context, use_llm=False)

    vin = parsed.vin or ""
    while not vin:
        v = input("\nNo VIN found in your paste — enter the 17-char VIN: ").strip().upper()
        if is_valid_vin(v):
            vin = v
        else:
            print("  ↳ not a valid 17-char VIN")

    mileage = parsed.mileage
    if mileage is None:
        raw = input("Mileage (optional, Enter to skip): ").strip().replace(",", "")
        mileage = int(raw) if raw.isdigit() else None
    return vin, mileage, context


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # Color on only for an interactive terminal (keeps pipes/screenshots-to-file clean).
    set_color(sys.stdout.isatty() and not args.no_color)

    if args.list_checks:
        from vin_checker.logstore import render_log

        print(render_log())
        return 0

    context = ""
    if not args.vin and not args.listing:
        vin, mileage, context = _interactive()
        mileage = args.mileage or mileage
    elif args.listing:
        context = args.listing.read_text(errors="ignore")
        from vin_checker.listing_parse import parse_listing

        parsed = parse_listing(context, use_llm=not args.no_llm)
        if not parsed.vin:
            sys.exit("Could not find a VIN in the listing. Use --vin.")
        vin, mileage = parsed.vin, (args.mileage or parsed.mileage)
    else:
        vin, mileage = args.vin, args.mileage
    if args.context:
        context = args.context.read_text(errors="ignore")

    # Skipped the VIN and/or mileage prompt? Pull them from the pasted text
    # (regex, no LLM) so the card header, comps, and odometer-rollback check work.
    if context and (not vin or mileage is None):
        from vin_checker.listing_parse import parse_listing

        parsed = parse_listing(context, use_llm=False)
        vin = vin or (parsed.vin or "")
        if mileage is None:
            mileage = parsed.mileage
    if not vin:
        sys.exit("No VIN entered and none found in your paste — re-run and enter a VIN.")

    # Lightweight progress to stderr so you can see it's working (kept out of stdout
    # so --json stays clean and it won't clutter a screenshot of the card).
    def _progress(msg: str) -> None:
        print(f"  … {msg}", file=sys.stderr, flush=True)

    prog = None if args.json else _progress

    try:
        report = build_report(
            vin, mileage=mileage,
            statvin_fixture=args.statvin_fixture, vincheck_fixture=args.vincheck_fixture,
            progress=prog,
        )
    except DecodeError as e:
        sys.exit(f"Decode failed: {e}")

    if args.debug:
        diag = render_diagnostics(report)
        ctx_dump = (f"captured context: {len(context)} chars\n"
                    f"----- BEGIN CONTEXT -----\n{context}\n----- END CONTEXT -----"
                    if context else "captured context: (none)")
        print("\n" + diag, file=sys.stderr)
        print("\n" + ctx_dump, file=sys.stderr)
        try:
            from datetime import datetime
            logp = Path(__file__).resolve().parent / ".cache" / "debug.log"
            logp.parent.mkdir(exist_ok=True)
            with logp.open("a") as f:
                f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} "
                        f"{report.decoded.vin} ===\n{diag}\n{ctx_dump}\n")
        except OSError:
            pass

    if args.json:
        print(render_json(report))
        return 0

    from vin_checker.assess import pros_cons
    from vin_checker.research import research_car
    res = research_car(report.decoded, report.mileage, use_llm=not args.no_llm, progress=prog)
    pros, cons = pros_cons(report, context, use_llm=not args.no_llm, progress=prog)

    neg = None
    if context and not args.no_llm:
        from vin_checker.negotiate import negotiate_offer

        neg = negotiate_offer(report, context, progress=prog)

    if args.plain:
        print(render_text(report))
        print(render_research(res))
        print(render_proscons(pros, cons))
        if neg is not None:
            print(render_negotiation(neg))
    else:
        # Card (screenshot for seller) → web research + inspection checklist →
        # pros/cons → ready-to-send draft → your private offer (don't send that one).
        print("\n" + render_card(report))
        print(render_research(res))
        print(render_proscons(pros, cons))
        if neg is not None:
            print(render_draft(neg))
            print(render_offer_private(neg))

    # Log this check so `vincheck --list` can rank cars you're comparing.
    from vin_checker.logstore import save_check

    banner, _ = verdict(report)
    save_check({
        "vin": report.decoded.vin, "vehicle": report.decoded.full_name,
        "mileage": report.mileage, "verdict": banner,
        "value_median": report.comps.median,
        "offer": getattr(neg, "final_offer", None) if neg else None,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
