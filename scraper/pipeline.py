
from __future__ import annotations

import asyncio
import csv
import os
import random
from urllib.parse import urlparse
from typing import Callable, Dict, List, Optional, Tuple

from curl_cffi import requests
from bs4 import BeautifulSoup

from .config import SITE_CONFIGS, SiteConfig
from .parser import parse_product, _extract_sku as _extract_sku_from_html
from .shopify_catalog import ShopifyCatalogIndexer


CMS_MAP = {
    "Neto": "neto_default",
    "Shopify": "shopify_default",
    "WordPress (WooCommerce)": "wordpress_default",
}


# ──────────────────────────────────────────────────────────────────────────────
# Error helpers
# ──────────────────────────────────────────────────────────────────────────────

def _classify_error(status: int, html: str) -> str:
    """Return a typed, human-readable error string from an HTTP status + body."""
    body = (html or "")[:600].lower()
    if status == 404:
        return "not_found: page returned HTTP 404"
    if status == 403:
        if any(m in body for m in ("cloudflare", "cf-ray", "checking your browser", "just a moment")):
            return "blocked:cloudflare — Cloudflare challenge blocked this request"
        return "blocked:forbidden — HTTP 403 access denied"
    if status == 429:
        return "blocked:rate_limited — HTTP 429 too many requests"
    if status in (502, 503, 504):
        return f"server_error:{status} — upstream server error"
    snippet = (html or "")[:120].replace("\n", " ").strip()
    return f"HTTP {status}" + (f" — {snippet}..." if snippet else "")


def _is_empty_parse(data: dict) -> bool:
    """True when parse succeeded (no error) but extracted no usable product data.

    Requires at least 2 of {name, price, image_url} to be absent.  This threshold
    is high enough to catch generic pages (price + image missing even when a
    stray <h1> was grabbed) while not flagging real pages that simply have no
    listed price.
    """
    keys = ("name", "price", "image_url")
    missing = sum(1 for k in keys if not data.get(k))
    return missing >= 2


# Statuses that mean "the site refused us", as opposed to "the site answered,
# the product just isn't there".  Only these trip the circuit-breaker.
_BLOCK_STATUSES = (403, 429)


def _domain_of(origin: str) -> str:
    try:
        return urlparse(origin).netloc or origin
    except Exception:
        return origin


def _make_error_row(
    sku: Optional[str],
    url_in: Optional[str],
    error_msg: str,
    attempted: Optional[str] = None,
) -> dict:
    """Build a result row that records a failure with all fields set to None.

    ``attempted`` is the URL actually requested. Without it a 404 row is
    undiagnosable — you cannot tell a missing product from a malformed URL.
    """
    return {
        "sku": sku,
        "url": url_in,
        "product_url": attempted,
        "group_id": None,
        "variant_id": None,
        "all_variant_ids": [],
        "all_skus": [],
        "name": None,
        "category": None,
        "breadcrumbs": None,
        "price": None,
        "sale_price": None,
        "rrp": None,
        "discount_percent": None,
        "image_url": None,
        "error": error_msg,
    }


CHECKPOINT_COLUMNS = [
    "sku", "error", "product_url", "name", "price", "sale_price", "rrp",
    "discount_percent", "category", "breadcrumbs", "image_url",
    "image_url_2", "image_url_3", "image_url_4", "image_url_5", "url",
]


class _Checkpoint:
    """Append-as-you-go CSV so an interrupted run keeps the rows it finished.

    Re-opening the same path reads back the SKUs already done, which is what
    turns a second run into a resume.
    """

    def __init__(self, path: str):
        self.path = path
        self.done_skus: set = set()
        if os.path.exists(path):
            try:
                with open(path, newline="", encoding="utf-8") as f:
                    self.done_skus = {
                        (r.get("sku") or "").strip()
                        for r in csv.DictReader(f)
                        if (r.get("sku") or "").strip()
                    }
            except Exception:
                self.done_skus = set()
        self._f = open(path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._f, fieldnames=CHECKPOINT_COLUMNS, extrasaction="ignore")
        if not self.done_skus and self._f.tell() == 0:
            self._w.writeheader()
            self._f.flush()

    def write(self, row: dict) -> None:
        try:
            self._w.writerow(row)
            self._f.flush()  # survive a hard kill, not just a clean exit
        except Exception:
            pass  # a checkpoint must never take the run down with it

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


class UnresolvableHost(Exception):
    """The base URL's hostname does not resolve — almost always a typo."""


def _is_unresolvable_host(exc: Exception) -> bool:
    return "could not resolve host" in str(exc).lower()


def _bad_origin_msg(origin: Optional[str]) -> str:
    return (f"bad_origin: '{origin}' does not resolve — check the Base URL for a typo "
            "(e.g. '.co.au' instead of '.com.au')")


# ──────────────────────────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────────────────────────

async def _fetch(client: requests.AsyncSession, url: str, delay_ms: int, retries: int = 5) -> Tuple[int, str, str]:
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000.0)

    last_err = None
    for attempt in range(retries):
        try:
            r = await client.get(url, allow_redirects=True, timeout=40.0)
            # 403 is deliberately absent: a Cloudflare challenge is a deterministic
            # verdict on this IP/fingerprint, so retrying the same session only
            # burns ~35s per SKU to fail again.
            if r.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                wait_time = (attempt + 1) * 3 + random.uniform(0.5, 2.0)
                await asyncio.sleep(wait_time)
                continue
            return r.status_code, r.text, str(r.url)
        except Exception as e:
            last_err = e
            # A hostname that does not resolve will not start resolving on retry.
            # Usually a typo in the base URL, so surface it now rather than
            # spending 5 lookups on every SKU in the list.
            if _is_unresolvable_host(e):
                raise UnresolvableHost(str(e)) from e
            if attempt < retries - 1:
                await asyncio.sleep((attempt + 1) * 2)
                continue

    if last_err is not None:
        raise last_err
    return 500, "", url


# ──────────────────────────────────────────────────────────────────────────────
# Config helpers
# ──────────────────────────────────────────────────────────────────────────────

def _cfg_for_choice(cms_choice: Optional[str]) -> Optional[SiteConfig]:
    if not cms_choice or cms_choice.startswith("Auto"):
        return None
    key = CMS_MAP.get(str(cms_choice))
    return SITE_CONFIGS.get(str(key)) if key else None


def _build_url_for_sku(
    sku: Optional[str],
    url: Optional[str],
    cms_choice: Optional[str],
    origin: Optional[str],
    url_pattern: Optional[str],
) -> Optional[str]:
    if url:
        return url
    if not sku:
        return None
    # A pasted base URL often ends in "/", which produced "example.com//p/SKU".
    # Servers tolerate it; humans reading the CSV shouldn't have to.
    origin = origin.rstrip("/") if origin else origin
    if cms_choice == "Shopify" and not url_pattern:
        # Shopify SKUs aren't in the URL, so fall back to the storefront search.
        return f"{origin}/search?type=product&q={sku}" if origin else None
    if cms_choice == "WordPress (WooCommerce)" and not url_pattern:
        return None
    if url_pattern and "{sku}" in url_pattern:
        return url_pattern.format(sku=sku)
    if cms_choice == "Neto" and origin:
        return f"{origin}/p/{sku}"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main scraping entry point
# ──────────────────────────────────────────────────────────────────────────────

async def _warm_session(client: requests.AsyncSession, origin: str) -> bool:
    """
    Visit the site homepage to pick up cookies and let Cloudflare mark the session
    as "human-initiated" before we start hitting product pages.
    Returns True if the homepage responded with HTTP 200.
    """
    try:
        r = await client.get(origin.rstrip("/") + "/", timeout=15.0, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


async def scrape_items(
    items: List[Dict[str, Optional[str]]],
    cms_choice: Optional[str],
    origin: Optional[str],
    url_pattern: Optional[str],
    concurrency: int,
    delay_ms: int,
    indexer: Optional[ShopifyCatalogIndexer] = None,
    fast_mode: bool = False,
    on_progress: Optional[Callable] = None,
    checkpoint_path: Optional[str] = None,
) -> List[Dict]:
    """
    Scrape a list of SKU/URL items.

    on_progress(completed: int, total: int, sku: str, state: str) is called after
    each item finishes. ``state`` is "completed" or "failed".

    checkpoint_path, when given, receives every row the moment it is produced, so
    an interrupted run leaves its completed work on disk instead of losing it.
    Items whose SKU is already in that file are skipped, which makes a re-run a
    resume.
    """
    results: List[Dict] = []
    completed_count = 0

    checkpoint = _Checkpoint(checkpoint_path) if checkpoint_path else None
    if checkpoint and checkpoint.done_skus:
        items = [r for r in items if (r.get("sku") or "") not in checkpoint.done_skus]

    total_count = len(items)

    # Per-domain consecutive-failure counter for a lightweight circuit-breaker.
    # If a domain blocks 5 SKUs in a row without a single success, subsequent
    # requests are skipped with a "circuit_open" error rather than hammering it.
    _domain_failures: Dict[str, int] = {}
    _CIRCUIT_THRESHOLD = 5

    # Set once the base URL is shown not to resolve; every later item short-circuits.
    _bad_origin: List[Optional[str]] = [None]

    if cms_choice == "Shopify" and origin and not indexer:
        try:
            indexer = ShopifyCatalogIndexer(origin)
            print("Indexing Shopify Catalog… (this may take a moment)")
            await indexer.fetch_catalog()
            print(f"Indexed {len(indexer.catalog)} variants.")
        except Exception as e:
            print(f"Catalog indexing failed: {e}")
            indexer = None

    async with requests.AsyncSession(impersonate="chrome") as client:
        # Warm the session with a homepage visit for Neto so Cloudflare's
        # first-party cookie is set before we begin hitting product pages.
        if cms_choice == "Neto" and origin:
            await _warm_session(client, origin)
            if delay_ms:
                await asyncio.sleep(max(delay_ms / 1000.0, 1.0))

        sem = asyncio.Semaphore(concurrency)

        async def handle(row: Dict[str, Optional[str]]) -> None:
            nonlocal completed_count
            sku = row.get("sku")
            url_in = row.get("url")
            url = _build_url_for_sku(sku, url_in, cms_choice, origin, url_pattern)

            # Track the result produced by this handle invocation.
            _this_result: List[Optional[Dict]] = [None]

            def _append(item: dict) -> None:
                _this_result[0] = item
                results.append(item)
                if checkpoint:
                    checkpoint.write(item)

            try:
                if not url:
                    _append(_make_error_row(sku, url_in, "No URL could be constructed (supply a base URL or pattern)."))
                    return

                async with sem:
                    try:
                        # ── Shopify catalog path ──────────────────────────
                        if cms_choice == "Shopify" and indexer:
                            catalog_data = indexer.lookup_sku(sku)
                            if not catalog_data:
                                _append(_make_error_row(sku, url_in, "Strict match failed: SKU not found in site catalog."))
                                return

                            target_url = catalog_data["product_url"]
                            price = catalog_data.get("price")
                            rrp = catalog_data.get("rrp")
                            discount = None
                            try:
                                if price and rrp:
                                    p_v, r_v = float(price), float(rrp)
                                    if r_v > p_v:
                                        discount = round((1 - (p_v / r_v)) * 100)
                            except Exception:
                                pass

                            if fast_mode:
                                _append({
                                    "sku": sku, "url": url_in, "product_url": target_url,
                                    "group_id": catalog_data.get("product_id"),
                                    "variant_id": catalog_data.get("variant_id"),
                                    "all_variant_ids": catalog_data.get("all_variant_ids", []),
                                    "name": catalog_data.get("name"),
                                    "category": catalog_data.get("product_type"),
                                    "breadcrumbs": None,
                                    "price": price, "sale_price": catalog_data.get("sale_price"),
                                    "rrp": rrp, "discount_percent": discount,
                                    "image_url": catalog_data.get("image_url"),
                                    "image_url_2": catalog_data.get("image_url_2"),
                                    "image_url_3": catalog_data.get("image_url_3"),
                                    "image_url_4": catalog_data.get("image_url_4"),
                                    "image_url_5": catalog_data.get("image_url_5"),
                                    "error": None,
                                })
                                return

                            status, html, final_url = await _fetch(client, target_url, delay_ms)
                            if status != 200:
                                _append({
                                    "sku": sku, "url": url_in, "product_url": target_url,
                                    "group_id": catalog_data.get("product_id"),
                                    "variant_id": catalog_data.get("variant_id"),
                                    "all_variant_ids": catalog_data.get("all_variant_ids", []),
                                    "name": catalog_data.get("name"),
                                    "category": catalog_data.get("product_type"),
                                    "breadcrumbs": None,
                                    "price": price, "sale_price": catalog_data.get("sale_price"),
                                    "rrp": rrp, "discount_percent": discount,
                                    "image_url": catalog_data.get("image_url"),
                                    "image_url_2": catalog_data.get("image_url_2"),
                                    "image_url_3": catalog_data.get("image_url_3"),
                                    "image_url_4": catalog_data.get("image_url_4"),
                                    "image_url_5": catalog_data.get("image_url_5"),
                                    "error": f"Page fetch failed ({status}), using catalog data.",
                                })
                                return

                            try:
                                cfg = _cfg_for_choice(cms_choice)
                                data = await asyncio.wait_for(
                                    asyncio.to_thread(parse_product, html, final_url, cfg, sku),
                                    timeout=30,
                                )
                                if not data.get("price"):
                                    data["price"] = price
                                if not data.get("sale_price"):
                                    data["sale_price"] = catalog_data.get("sale_price")
                                if not data.get("rrp"):
                                    data["rrp"] = rrp
                                if not data.get("image_url"):
                                    data["image_url"] = catalog_data.get("image_url")
                                for i in range(2, 6):
                                    k = f"image_url_{i}"
                                    if not data.get(k):
                                        data[k] = catalog_data.get(k)
                                if not data.get("name"):
                                    data["name"] = catalog_data.get("name")
                                if not data.get("category"):
                                    data["category"] = catalog_data.get("product_type")
                                if catalog_data.get("product_id"):
                                    data["group_id"] = catalog_data.get("product_id")
                                if catalog_data.get("variant_id"):
                                    data["variant_id"] = catalog_data.get("variant_id")
                                if data.get("discount_percent") is None:
                                    p, r = data.get("price"), data.get("rrp")
                                    if p and r:
                                        try:
                                            if float(r) > 0:
                                                data["discount_percent"] = round(float((1 - float(p) / float(r)) * 100), 2)
                                        except Exception:
                                            pass
                                data["sku"] = sku
                                _append(data)
                            except Exception as e:
                                _append({
                                    "sku": sku, "url": url_in, "product_url": target_url,
                                    "group_id": catalog_data.get("product_id"),
                                    "variant_id": catalog_data.get("variant_id"),
                                    "all_variant_ids": [],
                                    "name": catalog_data.get("name"),
                                    "category": catalog_data.get("product_type"),
                                    "breadcrumbs": None,
                                    "price": price, "sale_price": catalog_data.get("sale_price"),
                                    "rrp": rrp, "discount_percent": None,
                                    "image_url": catalog_data.get("image_url"),
                                    "image_url_2": catalog_data.get("image_url_2"),
                                    "image_url_3": catalog_data.get("image_url_3"),
                                    "image_url_4": catalog_data.get("image_url_4"),
                                    "image_url_5": catalog_data.get("image_url_5"),
                                    "error": f"parse_error: {e} (using catalog data)",
                                })

                        # ── Neto / WooCommerce / direct-URL path ──────────
                        else:
                            if _bad_origin[0] is not None:
                                _append(_make_error_row(
                                    sku, url_in, _bad_origin_msg(_bad_origin[0]), url))
                                return

                            # Circuit-breaker: skip if origin is confirmed blocked
                            if origin:
                                domain = _domain_of(origin)
                                if _domain_failures.get(domain, 0) >= _CIRCUIT_THRESHOLD:
                                    _append(_make_error_row(
                                        sku, url_in,
                                        f"circuit_open: {domain} refused {_CIRCUIT_THRESHOLD} requests in a row "
                                        "(bot protection). This is a network problem, not a data problem — "
                                        "re-run from a residential connection rather than a cloud host.",
                                        url,
                                    ))
                                    return

                            status, html, final_url = await _fetch(client, url, delay_ms)

                            # Neto URL fallback chain: try alternative patterns on 404
                            if status == 404 and cms_choice == "Neto" and origin and sku:
                                cfg_obj = _cfg_for_choice(cms_choice)
                                patterns = getattr(cfg_obj, "url_patterns", []) if cfg_obj else []
                                tried = {url}
                                for pat in patterns:
                                    candidate = f"{origin.rstrip('/')}{pat.format(sku=sku)}"
                                    if candidate in tried:
                                        continue
                                    tried.add(candidate)
                                    fb_status, fb_html, fb_url = await _fetch(client, candidate, delay_ms)
                                    if fb_status == 200:
                                        status, html, final_url = fb_status, fb_html, fb_url
                                        break

                            if status != 200:
                                # Only a *refusal* counts toward the circuit-breaker.
                                # A 404 means the site answered us perfectly well and the
                                # product is simply gone — the expected result when auditing
                                # discontinued SKUs, and no reason to abandon the run.
                                if origin:
                                    domain = _domain_of(origin)
                                    if status in _BLOCK_STATUSES:
                                        _domain_failures[domain] = _domain_failures.get(domain, 0) + 1
                                    else:
                                        _domain_failures[domain] = 0
                                _append(_make_error_row(sku, url_in, _classify_error(status, html), final_url or url))
                                return

                            # Successful fetch — reset circuit-breaker counter for this domain
                            if origin:
                                _domain_failures[_domain_of(origin)] = 0

                            try:
                                # An unrecognised CMS falls back to the Neto profile; its
                                # selectors are the broadest, and structured.py backfills
                                # whatever they miss.
                                cfg = _cfg_for_choice(cms_choice) or SITE_CONFIGS["neto_default"]
                                data = await asyncio.wait_for(
                                    asyncio.to_thread(parse_product, html, final_url, cfg, sku),
                                    timeout=30,
                                )

                                # SKU mismatch guard (Neto only).
                                # parse_product uses the *requested* SKU when one is supplied, so
                                # we re-extract directly from the page to get the *page-side* SKU.
                                if cms_choice == "Neto" and not data.get("error") and sku:
                                    page_cfg = _cfg_for_choice(cms_choice)
                                    page_soup = BeautifulSoup(html, "lxml")
                                    page_sku = _extract_sku_from_html(page_soup, final_url, page_cfg)
                                    if page_sku and page_sku.strip().lower() != sku.strip().lower():
                                        data["error"] = (
                                            f"parse_mismatch: requested '{sku}' but page contains SKU '{page_sku}'"
                                        )

                                # Empty-parse guard: HTTP 200 but nothing extracted
                                if not data.get("error") and _is_empty_parse(data):
                                    data["error"] = (
                                        "parse_empty: page loaded (HTTP 200) but no product data extracted"
                                        " — possible bot block, homepage redirect, or selector mismatch"
                                    )

                                data.setdefault("sku", sku)
                                _append(data)

                            except asyncio.TimeoutError:
                                _append(_make_error_row(sku, url_in, "parse_timeout: parser timed out after 30s", url))
                            except Exception as e:
                                _append(_make_error_row(sku, url_in, f"parse_error: {e}", url))

                    except UnresolvableHost:
                        # One typo shouldn't cost a DNS lookup per SKU.
                        _bad_origin[0] = origin
                        _append(_make_error_row(sku, url_in, _bad_origin_msg(origin), url))
                    except Exception as e:
                        _append(_make_error_row(sku, url_in, f"request_failed: {e}", url))

            finally:
                completed_count += 1
                if on_progress:
                    r = _this_result[0] or {}
                    state = "failed" if r.get("error") else "completed"
                    on_progress(completed_count, total_count, sku or "", state)

        await asyncio.gather(*(handle(r) for r in items))

    if checkpoint:
        checkpoint.close()

    # gather() finishes in completion order; callers expect input order.
    order = {(r.get("sku") or "", r.get("url") or ""): i for i, r in enumerate(items)}
    results.sort(key=lambda d: order.get((d.get("sku") or "", d.get("url") or ""), len(order)))
    return results
