
from __future__ import annotations

import asyncio
import re
import json
import random
from urllib.parse import urlparse, urljoin, parse_qs
from typing import Callable, Dict, List, Optional, Tuple

from curl_cffi import requests
from bs4 import BeautifulSoup

from .config import SITE_CONFIGS, SiteConfig
from .parser import parse_product, _extract_sku as _extract_sku_from_html
from . import auto_parser
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


def _make_error_row(sku: Optional[str], url_in: Optional[str], error_msg: str) -> dict:
    """Build a result row that records a failure with all fields set to None."""
    return {
        "sku": sku,
        "url": url_in,
        "product_url": None,
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
            if r.status_code in (403, 429, 502, 503, 504) and attempt < retries - 1:
                wait_time = (attempt + 1) * 3 + random.uniform(0.5, 2.0)
                await asyncio.sleep(wait_time)
                continue
            return r.status_code, r.text, str(r.url)
        except Exception as e:
            last_err = e
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


def _cfg_key_for_choice(cms_choice: Optional[str]) -> Optional[str]:
    if not cms_choice or cms_choice.startswith("Auto"):
        return None
    return CMS_MAP.get(str(cms_choice))


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
    if cms_choice == "Shopify" and not url_pattern:
        if origin:
            return f"{origin}/search?type=product&q={sku}"
        return None
    if cms_choice in ("Shopify", "WordPress (WooCommerce)") and not url_pattern:
        return None
    if url_pattern and "{sku}" in url_pattern:
        return url_pattern.format(sku=sku)
    if cms_choice == "Neto" and origin:
        return f"{origin}/p/{sku}"
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Link extraction helpers (page-crawler path — unchanged)
# ──────────────────────────────────────────────────────────────────────────────

def _same_site(href: str, base: str) -> bool:
    try:
        a = urlparse(href)
        b = urlparse(base)
        return (a.netloc or b.netloc) == b.netloc
    except Exception:
        return True


def _is_junk_href(href: str) -> bool:
    if not href:
        return True
    href = href.strip()
    if href in ("/", "#"):
        return True
    low = href.lower()
    return low.startswith(("javascript:", "mailto:", "tel:", "data:", "blob:", "about:"))


def _normalise_href(href: str, base_url: str) -> Optional[str]:
    if _is_junk_href(href):
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    low = href.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return href
    try:
        absu = urljoin(base_url, href)
    except Exception:
        return None
    low = absu.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        return None
    return absu


def _extract_link_url(a, base_url: str) -> Optional[str]:
    href = a.get("href") or a.get("data-href") or a.get("data-url") or a.get("data-product-url")
    if not href:
        onclick = a.get("onclick")
        if onclick:
            m = re.search(r"location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", onclick)
            if m:
                href = m.group(1)
    return _normalise_href(href, base_url) if href else None


def _looks_like_product_url(href: str, cfg_key: Optional[str]) -> bool:
    p = urlparse(href)
    path = p.path or ""
    qs = parse_qs(p.query or "")
    if any(k in qs for k in ("product", "sku", "code", "id", "item", "prod", "variant")):
        return True
    if cfg_key == "shopify_default":
        return "/products/" in path
    if cfg_key == "wordpress_default":
        return "/product/" in path
    if ("/product/" in path) or ("/p/" in path):
        return True
    segs = [s for s in path.split("/") if s]
    return len(segs) >= 3


def _product_link_selectors(cfg_key: Optional[str]) -> list:
    if cfg_key == "shopify_default":
        return [
            "a[href*='/products/']",
            ".product-card a[href], .product-item a[href], .grid-product__content a[href]",
        ]
    if cfg_key == "wordpress_default":
        return [
            ".products a.woocommerce-LoopProduct-link",
            ".product a.woocommerce-LoopProduct-link",
            ".product a[href*='/product/']",
        ]
    return [
        "a[href*='/product/']",
        "a[href*='/p/']",
        ".product a[href], .product [data-href], .product [data-url], .product [data-product-url], .product [onclick]",
        ".product-item a[href], .product-item [data-href], .product-item [data-url], .product-item [data-product-url], .product-item [onclick]",
        ".product-title a[href], .product-title [data-href], .product-title [data-url], .product-title [data-product-url], .product-title [onclick]",
        "h3 a[href], h4 a[href], h5 a[href]",
    ]


def _get_origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _extract_candidate_skus(soup: BeautifulSoup) -> list:
    skus: set = set()

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            if isinstance(d, dict) and d.get("@type") in ("Product", "Offer"):
                sku = d.get("sku") or (d.get("itemOffered", {}) or {}).get("sku")
                if isinstance(sku, (str, int)) and str(sku).strip():
                    skus.add(str(sku).strip())
            if isinstance(d, dict) and d.get("@type") == "ItemList":
                for it in d.get("itemListElement") or []:
                    if isinstance(it, dict):
                        item = it.get("item") or {}
                        if isinstance(item, dict):
                            sku = item.get("sku")
                            if isinstance(sku, (str, int)) and str(sku).strip():
                                skus.add(str(sku).strip())

    for attr in ["data-sku", "data-code", "data-product-code", "data-product", "data-id"]:
        for el in soup.select(f"[{attr}]"):
            val = el.get(attr)
            if isinstance(val, (str, int)) and str(val).strip():
                skus.add(str(val).strip())

    for el in soup.select(".sku, .code, .product-code, .product_sku, [class*='sku'], [class*='code']"):
        t = el.get_text(" ", strip=True)
        if t:
            m = re.search(r"(?:SKU|Code)\s*[:#-]?\s*([A-Za-z0-9._-]{3,})", t, re.I)
            if m:
                skus.add(m.group(1).strip())

    for el in soup.select(".product, .product-item, .product-tile, li, .grid-item")[:500]:
        t = el.get_text(" ", strip=True)
        if not t:
            continue
        for m in re.finditer(r"\b[A-Za-z0-9][A-Za-z0-9._-]{2,}\b", t):
            tok = m.group(0)
            if len(tok) < 3 or len(tok) > 40:
                continue
            if tok.lower() in ("add", "view", "sale", "price", "cart", "colour", "color", "size"):
                continue
            if not re.search(r"\d", tok):
                continue
            skus.add(tok)

    return list(dict.fromkeys(skus))


def _jsonld_product_urls(soup: BeautifulSoup, base_url: str) -> list:
    urls: list = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            if not isinstance(d, dict):
                continue
            if d.get("@type") == "ItemList":
                for it in d.get("itemListElement") or []:
                    href = None
                    if isinstance(it, dict):
                        item = it.get("item") or it.get("url")
                        if isinstance(item, dict):
                            href = item.get("url")
                        elif isinstance(item, str):
                            href = item
                    if isinstance(href, str):
                        if href.startswith("//"):
                            href = "https:" + href
                        elif href.startswith("/"):
                            href = urljoin(base_url, href)
                        urls.append(href)
            if d.get("@type") == "Product":
                href = d.get("url")
                if isinstance(href, str):
                    if href.startswith("//"):
                        href = "https:" + href
                    elif href.startswith("/"):
                        href = urljoin(base_url, href)
                    urls.append(href)
    out, seen = [], set()
    for u in urls:
        if not u or not _same_site(u, base_url):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _find_product_links(soup: BeautifulSoup, base_url: str, config_name: Optional[str]) -> List[str]:
    sel = _product_link_selectors(config_name)
    seen, links = set(), []
    for s in sel:
        for a in soup.select(s):
            href = _extract_link_url(a, base_url)
            if not href:
                continue
            if not _same_site(href, base_url) or not _looks_like_product_url(href, config_name):
                continue
            if href.rstrip("/") == base_url.rstrip("/"):
                continue
            if href not in seen:
                seen.add(href)
                links.append(href)
    for u in _jsonld_product_urls(soup, base_url):
        u = _normalise_href(u, base_url) if u else None
        if not u:
            continue
        if not _looks_like_product_url(u, config_name):
            continue
        if u not in seen and _same_site(u, base_url) and u.rstrip("/") != base_url.rstrip("/"):
            seen.add(u)
            links.append(u)

    if config_name == "shopify_default":
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/products/" in href:
                full = href if href.startswith("http") else f"{base_url.rstrip('/')}{href}"
                links.append(full)
    return list(set(links))


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
) -> List[Dict]:
    """
    Scrape a list of SKU/URL items.

    on_progress(completed: int, total: int, sku: str, state: str) is called after
    each item finishes. ``state`` is "completed" or "failed".
    """
    results: List[Dict] = []
    completed_count = 0
    total_count = len(items)

    # Per-domain consecutive-failure counter for a lightweight circuit-breaker.
    # If a domain blocks 5 SKUs in a row without a single success, subsequent
    # requests are skipped with a "circuit_open" error rather than hammering it.
    _domain_failures: Dict[str, int] = {}
    _CIRCUIT_THRESHOLD = 5

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
                            # Circuit-breaker: skip if origin is confirmed blocked
                            if origin:
                                try:
                                    from urllib.parse import urlparse as _up
                                    domain = _up(origin).netloc
                                except Exception:
                                    domain = origin
                                if _domain_failures.get(domain, 0) >= _CIRCUIT_THRESHOLD:
                                    _append(_make_error_row(
                                        sku, url_in,
                                        f"circuit_open: domain {domain!r} blocked {_CIRCUIT_THRESHOLD}+ consecutive requests — skipping",
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
                                # Update circuit-breaker counter
                                if origin:
                                    try:
                                        from urllib.parse import urlparse as _up
                                        domain = _up(origin).netloc
                                    except Exception:
                                        domain = origin
                                    _domain_failures[domain] = _domain_failures.get(domain, 0) + 1
                                _append(_make_error_row(sku, url_in, _classify_error(status, html)))
                                return

                            # Successful fetch — reset circuit-breaker counter for this domain
                            if origin:
                                try:
                                    from urllib.parse import urlparse as _up
                                    domain = _up(origin).netloc
                                except Exception:
                                    domain = origin
                                _domain_failures[domain] = 0

                            try:
                                cfg = _cfg_for_choice(cms_choice)
                                if cfg:
                                    data = await asyncio.wait_for(
                                        asyncio.to_thread(parse_product, html, final_url, cfg, sku),
                                        timeout=30,
                                    )
                                else:
                                    data = await asyncio.wait_for(
                                        asyncio.to_thread(auto_parser.parse_auto, html, final_url, sku),
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
                                _append(_make_error_row(sku, url_in, "parse_timeout: parser timed out after 30s"))
                            except Exception as e:
                                _append(_make_error_row(sku, url_in, f"parse_error: {e}"))

                    except Exception as e:
                        _append(_make_error_row(sku, url_in, f"request_failed: {e}"))

            finally:
                completed_count += 1
                if on_progress:
                    r = _this_result[0] or {}
                    state = "failed" if r.get("error") else "completed"
                    on_progress(completed_count, total_count, sku or "", state)

        await asyncio.gather(*(handle(r) for r in items))

    return results


# ──────────────────────────────────────────────────────────────────────────────
# Page-crawler entry point (advanced / not the default team workflow)
# ──────────────────────────────────────────────────────────────────────────────

async def scrape_by_page(
    page_url: str,
    cms_choice: Optional[str],
    max_items: int,
    concurrency: int,
    delay_ms: int,
) -> List[Dict]:
    cfg_key = _cfg_key_for_choice(cms_choice)
    cfg = SITE_CONFIGS.get(cfg_key) if cfg_key else None

    async with requests.AsyncSession(impersonate="chrome") as client:
        status, html, final_url = await _fetch(client, page_url, delay_ms)
        if status != 200:
            return [{"product_url": page_url, "name": None, "category": None,
                     "breadcrumbs": None, "price": None, "sale_price": None,
                     "rrp": None, "discount_percent": None, "image_url": None,
                     "error": _classify_error(status, html)}]

        soup = BeautifulSoup(html, "lxml")
        links = _find_product_links(soup, final_url, cfg_key)

        if not links:
            candidate_skus = _extract_candidate_skus(soup)
            origin_base = _get_origin(final_url)
            links = [f"{origin_base}/p/{sku}" for sku in candidate_skus if sku]
            seen: set = set()
            links = [u for u in links if (u not in seen and not seen.add(u))]  # type: ignore[func-returns-value]

        if max_items > 0:
            links = links[:max_items]

        page_results: List[Dict] = []
        sem = asyncio.Semaphore(concurrency)

        async def handle_product(url: str) -> None:
            async with sem:
                s, h, fu = await _fetch(client, url, delay_ms)
            if s == 200:
                try:
                    if cfg:
                        page_results.append(await asyncio.wait_for(asyncio.to_thread(parse_product, h, fu, cfg), timeout=30))
                    else:
                        page_results.append(await asyncio.wait_for(asyncio.to_thread(auto_parser.parse_auto, h, fu), timeout=30))
                except asyncio.TimeoutError:
                    page_results.append({"product_url": url, "error": "parse_timeout: 30s"})
                except Exception as e:
                    page_results.append({"product_url": url, "error": f"parse_error: {e}"})
            else:
                page_results.append({"product_url": url, "error": _classify_error(s, h)})

        if links:
            await asyncio.gather(*(handle_product(u) for u in links))
        else:
            try:
                if cfg:
                    page_results.append(await asyncio.wait_for(asyncio.to_thread(parse_product, html, final_url, cfg), timeout=30))
                else:
                    page_results.append(await asyncio.wait_for(asyncio.to_thread(auto_parser.parse_auto, html, final_url), timeout=30))
            except asyncio.TimeoutError:
                page_results.append({"product_url": final_url, "error": "parse_timeout: 30s"})
            except Exception as e:
                page_results.append({"product_url": final_url, "error": f"parse_error: {e}"})

        return page_results
