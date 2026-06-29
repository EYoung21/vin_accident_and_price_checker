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
    render_draft,
    render_negotiation,
    render_offer_private,
    render_text,
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
    p.add_argument("--no-llm", action="store_true", help="disable LLM parsing + offer")
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
        data = event.data
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
    while True:
        vin = input("VIN (or press Enter to pull it from your paste): ").strip().upper()
        if not vin or is_valid_vin(vin):
            break
        print("  ↳ not a valid 17-char VIN — re-enter, or Enter to pull from the paste")
    raw_mi = input("Mileage (optional, Enter to skip): ").strip().replace(",", "")
    mileage = int(raw_mi) if raw_mi.isdigit() else None
    context = _read_block("\nPaste the listing — ask price, description, your chat with the seller:")
    return vin, mileage, context


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

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

    try:
        report = build_report(
            vin, mileage=mileage,
            statvin_fixture=args.statvin_fixture, vincheck_fixture=args.vincheck_fixture,
        )
    except DecodeError as e:
        sys.exit(f"Decode failed: {e}")

    if args.json:
        print(render_json(report))
        return 0

    neg = None
    if context and not args.no_llm:
        from vin_checker.negotiate import negotiate_offer

        neg = negotiate_offer(report, context)

    if args.plain:
        print(render_text(report))
        if neg is not None:
            print(render_negotiation(neg))
    else:
        # Shareable card first (screenshot this for the seller), then a ready-to-send
        # draft reply, then your private offer cheat-sheet (don't send that one).
        print("\n" + render_card(report))
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
