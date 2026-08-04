"""Fetch pages through a real Chrome window, for sites that refuse HTTP clients.

Measured 2026-08-04 against the metavparts/machter Neto stores, all from the
same residential IP within one minute:

    plain curl / curl_cffi (8 impersonation profiles)  ->  403, cf-mitigated: challenge
    Playwright headless (channel=chrome)               ->  403, "Just a moment..."
    Playwright headed   (channel=chrome)               ->  200, no challenge issued

So the window has to be real and visible. Headless is not a shortcut here, and
no TLS-impersonation profile substitutes for it. This inverts what was true on
2026-07-28, when curl_cffi passed these same stores — the sites tightened, so
treat the fast path as the thing that can fail, not as the thing that works.

One browser and one page serve the whole run: the challenge is never issued in
the first place, so there is no cookie to warm up and nothing to re-solve.
Images, fonts and media are aborted, which is most of the per-page time.

Playwright is imported lazily so the app runs without it installed until a site
actually forces this path.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Tuple

# Resource types that cost time and tell us nothing about the product data.
_SKIP_RESOURCES = {"image", "media", "font"}

_CHALLENGE_MARKERS = ("just a moment", "checking your browser", "attention required")


class BrowserUnavailable(RuntimeError):
    """Playwright or a usable Chrome is not installed."""


class BrowserFetcher:
    """Async context manager exposing the same (status, html, final_url) as _fetch."""

    def __init__(self, locale: str = "en-AU", headless: bool = False):
        self._locale = locale
        self._headless = headless
        self._pw = None
        self._browser = None
        self._page = None

    async def __aenter__(self) -> "BrowserFetcher":
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserUnavailable(
                "playwright is not installed — run: pip install playwright && playwright install chrome"
            ) from e

        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=self._headless, channel="chrome"
            )
        except Exception:
            # No system Chrome — bundled Chromium is worth a try, though it is
            # detected more often than the real thing.
            try:
                self._browser = await self._pw.chromium.launch(headless=self._headless)
            except Exception as e:
                await self._pw.stop()
                raise BrowserUnavailable(f"could not launch a browser: {e}") from e

        ctx = await self._browser.new_context(locale=self._locale)

        async def _skip_heavy_assets(route) -> None:
            if route.request.resource_type in _SKIP_RESOURCES:
                await route.abort()
            else:
                await route.continue_()

        await ctx.route("**/*", _skip_heavy_assets)
        self._page = await ctx.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        for closer in (
            getattr(self._browser, "close", None),
            getattr(self._pw, "stop", None),
        ):
            if closer:
                try:
                    await closer()
                except Exception:
                    pass

    async def _title(self) -> str:
        try:
            return (await self._page.title() or "").lower()
        except Exception:
            return ""

    async def fetch(self, url: str, delay_ms: int = 0) -> Tuple[int, str, str]:
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000.0)

        resp = await self._page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # A challenge should not appear at all, but if the site escalates, give it
        # a chance to clear itself rather than recording the interstitial as data.
        for _ in range(20):
            title = await self._title()
            if not any(m in title for m in _CHALLENGE_MARKERS):
                break
            await asyncio.sleep(1)

        status = resp.status if resp is not None else 0
        html = await self._page.content()
        return status, html, self._page.url


def is_block_status(status: Optional[int]) -> bool:
    """Whether a probe of the origin means we should switch to the browser.

    A network error is not evidence of bot protection, so None is not a block.
    """
    return status in (403, 429)
