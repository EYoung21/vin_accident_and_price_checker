# vin_accident_and_price_checker

A free, no-captcha replacement for the manual **NICB + KBB** workflow. Give it a
VIN (or a pasted Marketplace listing) and it returns one report:

- **Decode** — year/make/model/trim/engine (NHTSA vPIC, free, no key)
- **Private-party value** — a real low/median/high range built from actual
  listings near you (your DIY CarGurus IMV), not a single national opinion
- **History** — NMVTIS title brand + Copart/IAAI salvage-auction check
- **Safety** — open recalls + owner-complaint count for the model (NHTSA, free)
- **Optional LLM** — parse a pasted listing and rank candidates to go see

## Why this design (the honest version)

| Want | Source | Cost |
|------|--------|------|
| Decode | NHTSA vPIC | free, no key |
| Recalls/complaints | NHTSA | free, no key |
| Value | MarketCheck (500/mo free) + Craigslist | free tier |
| Title brand | vincheck.info (NMVTIS) | free |
| Salvage auction | stat.vin (Copart/IAAI) | free |

We deliberately **do not** scrape KBB (Akamai-protected, multi-step JS form) or
NICB (reCAPTCHA + 5/day cap). Their data is replaced by better free sources.

**The one gap:** a *minor repaired accident on a still-clean title* is invisible
to every free source — that needs a paid Carfax/AutoCheck (~$3–10), worth it only
for a car you're serious about.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml   # set your home location, radius, model, log dir
cp .env.example .env                  # secrets only: API keys + AWS creds
```

`config.toml` (non-secret, git-ignored) holds your home ZIP/lat-lon (for comps +
distance-to-you), search radius, Bedrock model, and log dir. Secrets stay in `.env`.

## Usage

```bash
vincheck                       # interactive: paste the listing+chat, it does the rest
vincheck --list                # your backlog of checked cars, ranked best-to-worst
vincheck --compare             # LLM compares all checked cars (or: --compare VIN1 VIN2)
python cli.py --vin <VIN> --json
```

Every run is logged to `car_log/` (top level): `log.jsonl` + a readable `cars.md`
table (verdict, value, offer, distance-to-you, location). A run gives you: a
shareable card, distance from home, web-grounded specs/0-60/problems + an in-person
inspection checklist, pros/cons, a draft reply, your private offer, then a
web-searching follow-up chat.

## The two sites that need a captured HTML sample

`stat.vin` and `vincheck.info` render their results with client-side JavaScript
behind bot protection, so a plain fetch reaches the page shell but not the report.
The tool detects this and reports **inconclusive** (it will never guess a verdict
from marketing boilerplate). To enable a definitive read:

1. Open the VIN's result page in your browser (stat.vin/cars/`<VIN>`, vincheck.info).
2. Grab the **rendered** DOM (DevTools → Elements → right-click `<html>` → Copy →
   Copy outerHTML — *not* View Source, which is the pre-JS shell). Save to:
   - `automation_html/statvin/<VIN>.html`
   - `automation_html/vincheck/<VIN>.html`
3. Rerun the same command — files are **auto-detected by VIN**, no flags needed.
   (Or pass `--statvin-fixture` / `--vincheck-fixture` for an explicit path.)

## Optional LLM layer (AWS Bedrock)

Used only for parsing messy pasted listings and ranking candidates. Set
`BEDROCK_MODEL_ID` (DeepSeek-V3 is cheap; Claude Haiku also fine) and AWS creds,
or run with `--no-llm`. Everything else is deterministic and needs no LLM.

## Project layout

```
cli.py                     entry point (interactive when run with no flags)
vin_checker/
  decode.py                vPIC VIN decode
  recalls.py               NHTSA recalls + complaints
  comps.py                 Auto.dev / MarketCheck / Craigslist value range
  history.py               stat.vin auction + vincheck.info title (+ fixture mode)
  negotiate.py             LLM offer engine (propose → push lower → hold)
  listing_parse.py         regex + optional LLM listing parser
  rank.py                  candidate ranking
  llm.py                   optional Bedrock wrapper (graceful fallback)
  report.py                assemble + render (card / text / JSON)
automation_html/
  statvin/<VIN>.html       captured stat.vin DOM (auto-detected by VIN)
  vincheck/<VIN>.html      captured vincheck.info DOM (auto-detected by VIN)
```
