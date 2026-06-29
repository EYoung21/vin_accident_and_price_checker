"""Tiny on-disk cache for JSON API responses.

This is the real answer to "what if I get rate-limited?" — re-checking the same
VIN within the TTL serves from disk and makes ZERO API calls. Keyed by URL+params
only (never headers), so API keys are never written to the cache key.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .config import CONFIG

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"


def _key(url: str, params: dict | None) -> Path:
    raw = url + "?" + urlencode(sorted((params or {}).items()))
    return CACHE_DIR / (hashlib.sha1(raw.encode()).hexdigest() + ".json")


def get_json(url, params=None, headers=None, ttl=86400, tolerate_status=False):
    """GET JSON with a disk cache. Raises requests exceptions on a live miss.

    tolerate_status=True returns a valid JSON body even on a 4xx/5xx — NHTSA's
    recalls API is known to send HTTP 400 with a perfectly good "0 results" body.
    """
    path = _key(url, params)
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass  # corrupt cache entry → refetch

    resp = requests.get(url, params=params, headers=headers, timeout=CONFIG.http_timeout)
    if not tolerate_status:
        resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()  # no JSON body → surface the HTTP error
        raise
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass  # caching is best-effort; never fail the request over it
    return data
