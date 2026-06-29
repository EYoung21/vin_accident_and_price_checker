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
    render_negotiation,
    render_offer_private,
    render_text,
)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Free VIN history + private-party value + offer")
    p.add_argument("--vin", help="17-character VIN (omit all flags for interactive mode)")
    p.add_argument("--listing", type=Path, help="text file with a pasted listing")
    p.add_argument("--mileage", type=int, help="odometer, tightens the comps")
    p.add_argument("--context", type=Path, help="text file: listing + seller chat → offer")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a card")
    p.add_argument("--plain", action="store_true", help="plain text instead of the card")
    p.add_argument("--no-llm", action="store_true", help="disable LLM parsing + offer")
    p.add_argument("--statvin-fixture", type=Path, help="captured stat.vin result HTML")
    p.add_argument("--vincheck-fixture", type=Path, help="captured vincheck.info HTML")
    return p.parse_args(argv)


def _read_block(prompt: str) -> str:
    print(prompt)
    print("(paste everything, then type END on its own line and press Enter)")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _interactive() -> tuple[str, int | None, str]:
    print("=== VIN checker — paste a car you're looking at ===\n")
    while True:
        vin = input("VIN: ").strip().upper()
        if is_valid_vin(vin):
            break
        print("  ↳ that's not a valid 17-char VIN, try again")
    raw_mi = input("Mileage (optional, Enter to skip): ").strip().replace(",", "")
    mileage = int(raw_mi) if raw_mi.isdigit() else None
    context = _read_block("\nPaste the listing — ask price, description, your chat with the seller:")
    return vin, mileage, context


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

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

    # If you skipped the mileage prompt but it's in the pasted listing, pull it out
    # (regex, no LLM) so the card header, comps, and odometer-rollback check work.
    if mileage is None and context:
        from vin_checker.listing_parse import parse_listing

        mileage = parse_listing(context, use_llm=False).mileage

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
        # Shareable card first (screenshot this for the seller), private offer after.
        print("\n" + render_card(report))
        if neg is not None:
            print(render_offer_private(neg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
