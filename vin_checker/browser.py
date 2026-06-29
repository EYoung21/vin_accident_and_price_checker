"""Headless-browser fetch for the Cloudflare/JS-walled history sites.

curl can't pass Cloudflare's "Just a moment" JS challenge or run the Livewire
JS that renders the result — a real browser can. This renders the page with
Playwright/Chromium, waits for Cloudflare to clear and the content to appear,
and returns the rendered DOM (which the existing parsers already handle).

Everything degrades gracefully: if Playwright isn't installed, Chromium can't be
provisioned, or Cloudflare hard-blocks, render() returns None and the caller
falls back to the manual-capture path.
"""

from __future__ import annotations

import subprocess
import sys

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _render_once(url: str, wait_for: str | None, settle_ms: int, timeout_ms: int) -> str | None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900},
                                  locale="en-US")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # Poll until Cloudflare's challenge clears (and the content selector shows).
            import time
            end = time.time() + timeout_ms / 1000
            while time.time() < end:
                low = page.content().lower()
                cleared = "just a moment" not in low and "challenge-platform" not in low
                if cleared and (not wait_for or page.query_selector(wait_for)):
                    break
                page.wait_for_timeout(800)
            page.wait_for_timeout(settle_ms)  # let Livewire/JS finish painting
            return page.content()
        finally:
            browser.close()


def render(url: str, wait_for: str | None = None, settle_ms: int = 3500,
           timeout_ms: int = 30000, progress=None) -> str | None:
    p = progress or (lambda *_: None)
    if not available():
        return None
    try:
        return _render_once(url, wait_for, settle_ms, timeout_ms)
    except Exception as e:
        # Most common first-run failure: the Chromium binary isn't installed yet.
        if "executable doesn't exist" in str(e).lower() or "playwright install" in str(e).lower():
            p("installing headless browser (one-time, ~150MB)")
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                           check=False)
            try:
                return _render_once(url, wait_for, settle_ms, timeout_ms)
            except Exception:
                return None
        return None
