"""Web-grounded research for a specific vehicle.

Pulls real search results (common problems, specs/0-60, what-to-inspect) and has
the Bedrock LLM synthesize them into a buyer-facing summary + an in-person
inspection checklist — so the answers are grounded in the web, not just the
model's memory. Degrades gracefully if search or the LLM is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm, websearch
from .decode import DecodedVin


@dataclass
class CarResearch:
    zero_to_sixty: str | None = None
    performance: str | None = None
    audio: str | None = None
    connectivity: str | None = None
    common_problems: list[str] = field(default_factory=list)
    inspect_in_person: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    available: bool = False


def _facts(d: DecodedVin, mileage: int | None) -> str:
    raw = d.raw
    bits = [d.full_name, f"{d.hp} hp" if d.hp else None, d.engine, d.body_class,
            d.drive_type, d.transmission or raw.get("TransmissionStyle"),
            f"~{mileage:,} miles" if mileage else None]
    return ", ".join(b for b in bits if b)


def research_car(decoded: DecodedVin, mileage: int | None = None,
                 use_llm: bool = True, progress=None) -> CarResearch:
    res = CarResearch()
    if not (use_llm and llm.available()):
        return res

    p = progress or (lambda *_: None)
    ymm = " ".join(x for x in (decoded.year, decoded.make, decoded.model) if x)
    eng = decoded.engine or ""

    p("researching specs, problems + inspection (web)")
    hits: list[dict] = []
    for q in (f"{ymm} {eng} common problems reliability",
              f"{ymm} buying used what to look for inspection",
              f"{ymm} {eng} 0-60 specs audio bluetooth"):
        hits.extend(websearch.search(q, n=4))

    # Dedup + collect source links
    seen, snippets = set(), []
    for h in hits:
        link = h.get("link", "")
        if link and link not in seen:
            seen.add(link)
            res.sources.append(link)
        if h.get("snippet"):
            snippets.append(f"- {h.get('title','')}: {h['snippet']}")
    res.sources = res.sources[:6]

    grounding = "\n".join(snippets[:14]) or "(no web results — use general knowledge)"
    system = (
        "You are a precise used-car buying expert. Using the SEARCH RESULTS as your "
        "primary source (don't invent specifics they contradict), give concise buyer "
        "info for the EXACT vehicle. If results are thin, use general knowledge and "
        "stay cautious. "
        'Return ONLY JSON: {"zero_to_sixty":"e.g. ~8.8-9.5 s","performance":"one '
        'short line on how it drives","audio":"factory audio incl. premium option '
        '(e.g. Bose) and how to spot it","connectivity":"bluetooth/aux for this year; '
        'note aftermarket if none","common_problems":["<=5 short known issues for '
        'THIS engine/model, esp. by this mileage"],"inspect_in_person":["<=6 specific '
        'things to check/test-drive on THIS model given its known issues"]}.'
    )
    user = f"VEHICLE: {_facts(decoded, mileage)}\n\nSEARCH RESULTS:\n{grounding}"
    data, _ = llm.chat_json(system, [{"role": "user", "content": user}], max_tokens=1100)
    if not data:
        return res

    res.zero_to_sixty = data.get("zero_to_sixty")
    res.performance = data.get("performance")
    res.audio = data.get("audio")
    res.connectivity = data.get("connectivity")
    res.common_problems = [str(x) for x in (data.get("common_problems") or [])][:5]
    res.inspect_in_person = [str(x) for x in (data.get("inspect_in_person") or [])][:6]
    res.available = True
    return res
