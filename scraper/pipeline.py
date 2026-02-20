
from __future__ import annotations

import asyncio
import re
import json
from urllib.parse import urlparse
from typing import List, Dict, Optional, Tuple

import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs

from .config import SITE_CONFIGS, SiteConfig
from .parser import parse_product
from . import auto_parser
from .shopify_catalog import ShopifyCatalogIndexer

CMS_MAP = {
    "Neto": "neto_default",
    "Shopify": "shopify_default",
    "WordPress (WooCommerce)": "wordpress_default",
}

async def _fetch(client: httpx.AsyncClient, url: str, delay_ms: int) -> Tuple[int, str, str]:
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000.0)
    r = await client.get(url, follow_redirects=True, timeout=40.0)
    return r.status_code, r.text, str(r.url)

def _cfg_for_choice(cms_choice: Optional[str]) -> Optional[SiteConfig]:
    if not cms_choice or cms_choice.startswith("Auto"):
        return None
    key = CMS_MAP.get(cms_choice)
    return SITE_CONFIGS.get(key) if key else None

def _cfg_key_for_choice(cms_choice: Optional[str]) -> Optional[str]:
    if not cms_choice or cms_choice.startswith("Auto"):
        return None
    return CMS_MAP.get(cms_choice)

def _build_url_for_sku(sku: Optional[str], url: Optional[str],
                       cms_choice: Optional[str], origin: Optional[str],
                       url_pattern: Optional[str]) -> Optional[str]:
    if url:
        return url
    if not sku:
        return None
    if cms_choice == "Shopify" and not url_pattern:
        # Fallback to search if no pattern provided
        if origin:
            return f"{origin}/search?type=product&q={sku}"
        return None
    if cms_choice in ("Shopify", "WordPress (WooCommerce)") and not url_pattern:
        return None
    if url_pattern and "{sku}" in url_pattern:
        return (url_pattern or "").format(sku=sku)
    if cms_choice == "Neto" and origin:
        return f"{origin}/p/{sku}"
    return None

def _same_site(href: str, base: str) -> bool:
    try:
        a = urlparse(href); b = urlparse(base)
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
    # keep "?…" because some Neto themes link products via query params
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
    # query-driven product links (common on some Neto themes)
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

def _product_link_selectors(cfg_key: Optional[str]) -> list[str]:
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

def _extract_candidate_skus(soup: BeautifulSoup) -> list[str]:
    skus = set()

    # 1) JSON-LD Products with sku
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

    # 2) data attributes
    for attr in ["data-sku", "data-code", "data-product-code", "data-product", "data-id"]:
        for el in soup.select(f"[{attr}]"):
            val = el.get(attr)
            if isinstance(val, (str, int)) and str(val).strip():
                skus.add(str(val).strip())

    # 3) Visible text near SKU/Code
    for el in soup.select(".sku, .code, .product-code, .product_sku, [class*='sku'], [class*='code']"):
        t = el.get_text(" ", strip=True)
        if t:
            m = re.search(r"(?:SKU|Code)\s*[:#-]?\s*([A-Za-z0-9._-]{3,})", t, re.I)
            if m:
                skus.add(m.group(1).strip())

    # 4) Lightweight regex scan in product tiles
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


def _jsonld_product_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
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
            seen.add(u); out.append(u)
    return out

def _find_product_links(soup: BeautifulSoup, base_url: str, config_name: str) -> List[str]:
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
                seen.add(href); links.append(href)
    for u in _jsonld_product_urls(soup, base_url):
        u = _normalise_href(u, base_url) if u else None
        if not u:
            continue
        if not _looks_like_product_url(u, config_name):
            continue
        if u not in seen and _same_site(u, base_url) and u.rstrip("/") != base_url.rstrip("/"):
            seen.add(u); links.append(u)
    
    # Simple heuristic for Shopify search results
    if config_name == "shopify_default":
        # Look for links inside search result containers
        # Common themes use .grid-view-item__link, .product-card, etc.
        # But generic fallback: find all a href that contain /products/
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/products/" in href:
                # Clean up URL
                full = href if href.startswith("http") else f"{base_url.rstrip('/')}{href}"
                # Remove query params for cleaner matching? No, might need variant
                links.append(full)
    return list(set(links)) # Unique

async def scrape_items(items: List[Dict[str, Optional[str]]],
                       cms_choice: Optional[str],
                       origin: Optional[str],
                       url_pattern: Optional[str],
                       concurrency: int,
                       delay_ms: int,
                       indexer: Optional[ShopifyCatalogIndexer] = None,
                       fast_mode: bool = False) -> List[Dict]:
    results: List[Dict] = []
    
    # Initialize Catalog Indexer for Shopify IF NOT PROVIDED
    if cms_choice == "Shopify" and origin and not indexer:
        try:
            indexer = ShopifyCatalogIndexer(origin)
            print("Indexing Shopify Catalog... (this may take a moment)")
            await indexer.fetch_catalog()
            print(f"Indexed {len(indexer.catalog)} variants.")
        except Exception as e:
            print(f"Catalog indexing failed: {e}")
            indexer = None

    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    headers = {"user-agent": "Mozilla/5.0 SKU-Scraper/3.1 (+https://example.com)"}
    async with httpx.AsyncClient(limits=limits, headers=headers) as client:
        sem = asyncio.Semaphore(concurrency)

        async def handle(row: Dict[str, Optional[str]]):
            sku = row.get("sku")
            url_in = row.get("url")
            url = _build_url_for_sku(sku, url_in, cms_choice, origin, url_pattern)
            if not url:
                results.append({
                    "sku": sku, "url": url_in, "product_url": None,
                    "name": None, "category": None, "breadcrumbs": None,
                    "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                    "error": "No URL could be constructed (supply URL or pattern)."
                })
                return

            async with sem:
                try:
                    # CATALOG MODE for Shopify
                    if cms_choice == "Shopify" and indexer:
                        catalog_data = indexer.lookup_sku(sku)
                        if not catalog_data:
                            results.append({
                                "sku": sku, "url": url_in, "product_url": None,
                                "name": None, "category": None, "breadcrumbs": None,
                                "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                                "error": "Strict match failed: SKU not found in site catalog."
                            })
                            return
                        
                        # SKU found in catalog!
                        # We have the correct URL and some data.
                        target_url = catalog_data["product_url"]

                        # Calculate discount
                        price = catalog_data.get("price")
                        rrp = catalog_data.get("rrp")
                        discount = None
                        try:
                            if price and rrp:
                                price_val = float(price)
                                rrp_val = float(rrp)
                                if rrp_val > price_val:
                                    discount = round((1 - (price_val / rrp_val)) * 100)
                        except Exception as e:
                            # Silently fail if conversion fails
                            pass

                        # FAST MODE: Skip page fetch
                        if fast_mode:
                            results.append({
                                "sku": sku, "url": url_in, "product_url": target_url,
                                "group_id": catalog_data.get("product_id"),
                                "variant_id": catalog_data.get("variant_id"),
                                "all_variant_ids": catalog_data.get("all_variant_ids", []),
                                "name": catalog_data.get("name"),
                                "category": catalog_data.get("product_type"),
                                "breadcrumbs": None, # Not available in fast mode
                                "price": price,
                                "rrp": rrp,
                                "discount_percent": discount,
                                "image_url": catalog_data.get("image_url"),
                                "image_url_2": catalog_data.get("image_url_2"),
                                "image_url_3": catalog_data.get("image_url_3"),
                                "image_url_4": catalog_data.get("image_url_4"),
                                "image_url_5": catalog_data.get("image_url_5"),
                                "error": None
                            })
                            return

                        # Fetch the actual product page
                        status, html, final_url = await _fetch(client, target_url, delay_ms)
                        
                        if status != 200:
                             # Fallback to catalog data if page fetch fails
                            results.append({
                                "sku": sku, "url": url_in, "product_url": target_url,
                                "group_id": catalog_data.get("product_id"),
                                "variant_id": catalog_data.get("variant_id"),
                                "all_variant_ids": catalog_data.get("all_variant_ids", []),
                                "name": catalog_data.get("name"),
                                "category": catalog_data.get("product_type"),
                                "breadcrumbs": None,
                                "price": price,
                                "rrp": rrp,
                                "discount_percent": discount,
                                "image_url": catalog_data.get("image_url"),
                                "image_url_2": catalog_data.get("image_url_2"),
                                "image_url_3": catalog_data.get("image_url_3"),
                                "image_url_4": catalog_data.get("image_url_4"),
                                "image_url_5": catalog_data.get("image_url_5"),
                                "error": f"Page fetch failed ({status}), using catalog data."
                            })
                            return

                        # Parse the page
                        try:
                            cfg = _cfg_for_choice(cms_choice)
                            # Offload CPU-bound parsing to a thread
                            data = await asyncio.wait_for(asyncio.to_thread(parse_product, html, final_url, cfg, sku), timeout=30)
                            
                            # Merge/Overwrite with Catalog Data (Catalog is authoritative for Price/Name/Image usually)
                            # But Page is authoritative for Breadcrumbs.
                            # Let's trust Catalog for Price/Name/Image as it's API data.
                            # Actually, let's trust the Parser if it found data, but use Catalog as fallback/verification.
                            # User wants "Image URL, Breadcrumbs, Price, and Name".
                            
                            # If parser missed price, use catalog price
                            if not data.get("price"):
                                data["price"] = catalog_data.get("price")
                            if not data.get("rrp"):
                                data["rrp"] = catalog_data.get("rrp")
                            if not data.get("image_url"):
                                data["image_url"] = catalog_data.get("image_url")
                            # Merge extra images if missing
                            for i in range(2, 6):
                                key = f"image_url_{i}"
                                if not data.get(key):
                                    data[key] = catalog_data.get(key)
                                    
                            if not data.get("name"):
                                data["name"] = catalog_data.get("name")
                            if not data.get("category"):
                                data["category"] = catalog_data.get("product_type")
                            
                            # Always prioritize Catalog IDs as they are authoritative
                            if catalog_data.get("product_id"):
                                data["group_id"] = catalog_data.get("product_id")
                            if catalog_data.get("variant_id"):
                                data["variant_id"] = catalog_data.get("variant_id")
                                
                            # Merge all_variant_ids? Catalog doesn't easily give us *all* variants for a product unless we look them up.
                            # But we can trust the parser for this if it found them.
                            
                            # Recalculate Discount if missing (because we might have filled price/rrp from catalog)
                            if data.get("discount_percent") is None:
                                p = data.get("price")
                                r = data.get("rrp")
                                if p and r:
                                    try:
                                        p_val = float(p)
                                        r_val = float(r)
                                        if r_val > 0:
                                            data["discount_percent"] = round((1 - (p_val / r_val)) * 100, 2)
                                    except Exception:
                                        pass

                            # Ensure SKU is set
                            data["sku"] = sku
                            results.append(data)
                            return
                        except Exception as e:
                            results.append({
                                "sku": sku, "url": url_in, "product_url": target_url,
                                "group_id": catalog_data.get("product_id"),
                                "variant_id": catalog_data.get("variant_id"),
                                "all_variant_ids": [],
                                "name": catalog_data.get("name"),
                                "category": catalog_data.get("product_type"),
                                "breadcrumbs": None,
                                "price": catalog_data.get("price"),
                                "rrp": catalog_data.get("rrp"),
                                "discount_percent": None,
                                "image_url": catalog_data.get("image_url"),
                                "image_url_2": catalog_data.get("image_url_2"),
                                "image_url_3": catalog_data.get("image_url_3"),
                                "image_url_4": catalog_data.get("image_url_4"),
                                "image_url_5": catalog_data.get("image_url_5"),
                                "error": f"Parse error: {e} (using catalog data)"
                            })
                            return

                    # LEGACY / NON-SHOPIFY LOGIC
                    # 1) If it's a Search URL (and not Shopify Catalog mode), resolve it
                    if url and "/search?" in url and cms_choice == "Shopify":
                         # This path should technically be unreachable now if we force catalog for Shopify
                         # But keeping it for safety or if indexer failed?
                         # Let's assume if indexer is None, we fall back to this?
                         pass 
                    
                    # ... (rest of legacy logic if needed, but we are replacing it)
                    
                    # 2) Fetch the actual product page (fallback or direct URL)
                    status, html, final_url = await _fetch(client, url, delay_ms)
                except Exception as e:
                    results.append({
                        "sku": sku, "url": url, "product_url": None,
                        "name": None, "category": None, "breadcrumbs": None,
                        "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                        "error": f"Request failed: {e}"
                    })
                    return
            if status != 200:
                results.append({
                    "sku": sku, "url": url, "product_url": None,
                    "name": None, "category": None, "breadcrumbs": None,
                    "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                    "error": f"HTTP {status}"
                })
                return



            try:
                cfg = _cfg_for_choice(cms_choice)
                if cfg:
                    # Offload CPU-bound parsing to a thread
                    data = await asyncio.wait_for(asyncio.to_thread(parse_product, html, final_url, cfg, sku), timeout=30)
                else:
                    data = await asyncio.wait_for(asyncio.to_thread(auto_parser.parse_auto, html, final_url, sku), timeout=30)
                results.append(data)
            except asyncio.TimeoutError:
                 results.append({
                    "sku": sku, "url": url, "product_url": final_url,
                    "name": None, "category": None, "breadcrumbs": None,
                    "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                    "error": "Parse Timeout (30s)",
                })
            except Exception as e:
                results.append({
                    "sku": sku, "url": url, "product_url": final_url,
                    "name": None, "category": None, "breadcrumbs": None,
                    "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                    "error": f"Parse error: {e}",
                })


        await asyncio.gather(*(handle(r) for r in items))

    return results

async def scrape_by_page(page_url: str,
                         cms_choice: Optional[str],
                         max_items: int,
                         concurrency: int,
                         delay_ms: int) -> List[Dict]:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    headers = {"user-agent": "Mozilla/5.0 SKU-Scraper/3.1 (+https://example.com)"}
    cfg_key = _cfg_key_for_choice(cms_choice)
    cfg = SITE_CONFIGS.get(cfg_key) if cfg_key else None

    async with httpx.AsyncClient(limits=limits, headers=headers) as client:
        status, html, final_url = await _fetch(client, page_url, delay_ms)
        if status != 200:
            return [{
                "product_url": page_url,
                "name": None, "category": None, "breadcrumbs": None,
                "price": None, "rrp": None, "discount_percent": None, "image_url": None,
                "error": f"HTTP {status}"
            }]

        soup = BeautifulSoup(html, "lxml")
        links = _find_product_links(soup, final_url, cfg_key)

        if not links:
            # Neto (e.g. MJS) fallback: build product URLs from SKUs on the category page
            candidate_skus = _extract_candidate_skus(soup)
            origin = _get_origin(final_url)
            links = [f"{origin}/p/{sku}" for sku in candidate_skus if sku]
            seen = set()
            links = [u for u in links if (u not in seen and not seen.add(u))]
        
        # Limit items
        if max_items > 0:
            links = links[:max_items]

        results: List[Dict] = []
        sem = asyncio.Semaphore(concurrency)

        async def handle_product(url: str):
            async with sem:
                s, h, fu = await _fetch(client, url, delay_ms)
            if s == 200:
                try:
                    if cfg:
                        results.append(await asyncio.wait_for(asyncio.to_thread(parse_product, h, fu, cfg), timeout=30))
                    else:
                        results.append(await asyncio.wait_for(asyncio.to_thread(auto_parser.parse_auto, h, fu), timeout=30))
                except asyncio.TimeoutError:
                    results.append({'product_url': url, 'error': 'Parse Timeout (30s)'})
                except Exception as e:
                    results.append({'product_url': url, 'error': f'Parse error: {e}'})
            else:
                results.append({'product_url': url, 'error': f'HTTP {s}'})

        if links:
            await asyncio.gather(*(handle_product(u) for u in links))
        else:
            # If no links found, maybe it's a single product page?
            # Try parsing the page itself
            try:
                if cfg:
                    results.append(await asyncio.wait_for(asyncio.to_thread(parse_product, html, final_url, cfg), timeout=30))
                else:
                    results.append(await asyncio.wait_for(asyncio.to_thread(auto_parser.parse_auto, html, final_url), timeout=30))
            except asyncio.TimeoutError:
                results.append({'product_url': final_url, 'error': 'Parse Timeout (30s)'})
            except Exception as e:
                results.append({'product_url': final_url, 'error': f'Parse error: {e}'})

        return results
