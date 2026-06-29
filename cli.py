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
    render_distance,
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
    p.add_argument("--compare", nargs="*", metavar="VIN", default=None,
                   help="LLM compares cars you've checked (all by default; or list "
                        "VINs to compare just those, e.g. --compare WAUA.. WAUB..)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a card")
    p.add_argument("--plain", action="store_true", help="plain text instead of the card")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    p.add_argument("--no-llm", action="store_true", help="disable LLM parsing + offer")
    p.add_argument("--no-chat", action="store_true",
                   help="skip the follow-up chat after the report")
    p.add_argument("--debug", action="store_true",
                   help="print diagnostics (comp filtering, why history is unverified, errors)")
    p.add_argument("--statvin-fixture", type=Path, help="captured stat.vin result HTML")
    p.add_argument("--vincheck-fixture", type=Path, help="captured vincheck.info HTML")
    return p.parse_args(argv)


def _interactive() -> tuple[str, int | None, str]:
    print("=== VIN checker — paste a car you're looking at ===\n")
    # Paste FIRST (this is the collapse-aware block), then pull VIN + mileage out of
    # it. This way there's one place to paste and it always behaves like Claude Code.
    from vin_checker.promptio import read_block
    context = read_block(
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

    if args.compare is not None:
        from vin_checker.compare import compare_cars

        print(compare_cars(vins=args.compare or None, use_llm=not args.no_llm))
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

    # Seller location → distance from home (config.toml).
    dist = None
    if context and not args.no_llm:
        from vin_checker import geo

        if (loc := geo.extract_location(context)):
            if prog:
                prog(f"distance from {loc}")
            dist = geo.distance(loc)

    neg = None
    if context and not args.no_llm:
        from vin_checker.negotiate import negotiate_offer

        neg = negotiate_offer(report, context, progress=prog)

    if args.plain:
        print(render_text(report))
        print(render_distance(dist))
        print(render_research(res))
        print(render_proscons(pros, cons))
        if neg is not None:
            print(render_negotiation(neg))
    else:
        # Card (screenshot for seller) → distance → web research + inspection →
        # pros/cons → ready-to-send draft → your private offer (don't send that one).
        print("\n" + render_card(report))
        print(render_distance(dist))
        print(render_research(res))
        print(render_proscons(pros, cons))
        if neg is not None:
            print(render_draft(neg))
            print(render_offer_private(neg))
        # Follow-up Q&A (interactive only), briefed on everything + web search.
        if not args.no_llm and not args.no_chat:
            from vin_checker.chat import chat_loop
            chat_loop(report, research=res, neg=neg, pros=pros, cons=cons, context=context)

    # Log this check so `vincheck --list` can rank cars you're comparing.
    from vin_checker.logstore import save_check

    banner, _ = verdict(report)
    save_check({
        "vin": report.decoded.vin, "vehicle": report.decoded.full_name,
        "mileage": report.mileage, "verdict": banner,
        "value_median": report.comps.median,
        "offer": getattr(neg, "final_offer", None) if neg else None,
        "deal_agreed": getattr(neg, "deal_agreed", False) if neg else False,
        "location": (dist or {}).get("place"),
        "distance_mi": (dist or {}).get("drive_mi") or (dist or {}).get("straight_mi"),
        "drive_min": (dist or {}).get("drive_min"),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
