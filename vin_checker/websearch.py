"""Tiny web-search helper for grounding the research section in real results.

Prefers Serper (serper.dev) if SERPER_API_KEY is set (higher quality, free tier,
no credit card); otherwise falls back to DuckDuckGo (no key at all). Returns a
list of {title, snippet, link}. Everything degrades to [] on failure.
"""

from __future__ import annotations

import os

import requests

from . import http_cache
from .config import CONFIG

SERPER_URL = "https://google.serper.dev/search"


def _serper(query: str, n: int) -> list[dict]:
    key = os.getenv("SERPER_API_KEY")
    if not key:
        return []
    try:
        r = requests.post(SERPER_URL, headers={"X-API-KEY": key},
                          json={"q": query, "num": n}, timeout=CONFIG.http_timeout)
        r.raise_for_status()
        out = []
        for item in (r.json().get("organic") or [])[:n]:
            out.append({"title": item.get("title", ""), "snippet": item.get("snippet", ""),
                        "link": item.get("link", "")})
        return out
    except (requests.RequestException, ValueError):
        return []


def _ddg(query: str, n: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # older package name
        except ImportError:
            return []
    try:
        with DDGS() as ddg:
            return [{"title": r.get("title", ""), "snippet": r.get("body", ""),
                     "link": r.get("href", "")} for r in ddg.text(query, max_results=n)]
    except Exception:
        return []


def search(query: str, n: int = 5) -> list[dict]:
    """Cached web search. Serper if keyed, else DuckDuckGo, else []."""
    cache_key = f"websearch::{query}::{n}"
    # Reuse the JSON disk cache by faking a URL key (search results change slowly).
    import hashlib
    import json
    import time
    from pathlib import Path
    path = http_cache.CACHE_DIR / ("ws_" + hashlib.sha1(cache_key.encode()).hexdigest() + ".json")
    if path.exists() and (time.time() - path.stat().st_mtime) < 604800:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    results = _serper(query, n) or _ddg(query, n)
    if results:
        try:
            http_cache.CACHE_DIR.mkdir(exist_ok=True)
            Path(path).write_text(json.dumps(results))
        except OSError:
            pass
    return results
