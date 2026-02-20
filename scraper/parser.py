from __future__ import annotations
from bs4 import BeautifulSoup
from typing import Optional, List
from urllib.parse import urlparse, urljoin, unquote
import json, re
from .config import SiteConfig

def _clean_amount(s: Optional[str]) -> Optional[float]:
    if not s: return None
    m = re.search(r"[\d,.]+", s)
    if not m: return None
    val = m.group(0).replace(",", "")
    try:
        return float(val)
    except ValueError:
        try:
            return float(val.replace(".", "").replace(",", "."))
        except Exception:
            return None

def _slug_to_name(url_like: str | None) -> Optional[str]:
    if not url_like: return None
    try:
        p = urlparse(url_like)
        seg = p.path.rstrip("/").split("/")[-1]
        if not seg: return None
        seg = unquote(seg).replace("-", " ").replace("_", " ")
        seg = re.sub(r"\s+", " ", seg).strip()
        return seg or None
    except Exception:
        return None

def _doc_url(soup: BeautifulSoup) -> Optional[str]:
    el = soup.select_one("link[rel='canonical']")
    if el and el.get("href"): return el.get("href")
    el = soup.select_one('meta[property="og:url"]')
    return el.get("content") if el else None

def _normalise_img_url(raw: Optional[str], base_domain: Optional[str]) -> Optional[str]:
    if not raw: return None
    raw = raw.strip()
    if raw.startswith("//"): return "https:" + raw
    if raw.startswith("http:"): return raw.replace("http:", "https:", 1)
    if raw.startswith("/"): return f"https://{base_domain}{raw}" if base_domain else raw
    return raw

def _origin_from_url(u: str) -> str:
    try:
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""

def _is_share_image(url: str) -> bool:
    if not url: return False
    u = url.lower()
    if ".svg" in u: return True
    return ("social-share" in u) or ("social" in u and "share" in u)

# --- Extraction Functions ---

def _extract_price(soup: BeautifulSoup, config: SiteConfig) -> Optional[float]:
    # 0. Configured JS Pattern (Priority)
    if config and config.price_js_pattern:
        for s in soup.find_all("script"):
            if not s.string: continue
            m = re.search(config.price_js_pattern, s.string, re.DOTALL | re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "").strip())
                except ValueError:
                    pass

    # 1. Try JS "var item" (Klaviyo/Shopify)
    for s in soup.find_all("script"):
        if s.string and "var item" in s.string:
            # Try Price: "$..."
            m = re.search(r'Price\s*:\s*"([^"]+)"', s.string)
            if m:
                p = _clean_amount(m.group(1))
                if p: return p
            # Try Value: "..."
            m = re.search(r'Value\s*:\s*"([^"]+)"', s.string)
            if m:
                p = _clean_amount(m.group(1))
                if p: return p

    # 2. Try JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            if isinstance(d, dict):
                if d.get("@type") == "Product":
                    offers = d.get("offers")
                    if isinstance(offers, dict):
                        p = offers.get("price")
                        if p: return _clean_amount(str(p))
                    elif isinstance(offers, list):
                        for o in offers:
                            p = o.get("price")
                            if p: return _clean_amount(str(p))

    # 3. Config selector
    if config.price_selector:
        for sel in config.price_selector.split(","):
            el = soup.select_one(sel.strip())
            if el:
                val = el.get("content") or el.get_text(strip=True)
                p = _clean_amount(val)
                if p: return p
    
    # 4. Regex fallback
    if config.price_regex:
        found = soup.find(string=config.price_regex)
        if found:
            return _clean_amount(found)
    return None

def _extract_rrp(soup: BeautifulSoup, config: SiteConfig) -> Optional[float]:
    # 1. Try JS "var item"
    for s in soup.find_all("script"):
        if s.string and "var item" in s.string:
            m = re.search(r'CompareAtPrice\s*:\s*"([^"]+)"', s.string)
            if m:
                p = _clean_amount(m.group(1))
                if p and p > 0: return p

    if config.rrp_selector:
        for sel in config.rrp_selector.split(","):
            el = soup.select_one(sel.strip())
            if el:
                val = el.get("content") or el.get_text(strip=True)
                p = _clean_amount(val)
                if p: return p
    return None

def _extract_image(soup: BeautifulSoup, config: SiteConfig, base_url: str) -> Optional[str]:
    base_domain = _origin_from_url(base_url).replace("https://", "").replace("http://", "")
    
    # 1. Try JS "var item"
    for s in soup.find_all("script"):
        if s.string and "var item" in s.string:
            m = re.search(r'ImageURL\s*:\s*"([^"]+)"', s.string)
            if m:
                img = m.group(1).strip()
                norm = _normalise_img_url(img, base_domain)
                if norm and not _is_share_image(norm):
                    return norm

    # 2. Config selector
    if config.image_selector:
        for sel in config.image_selector.split(","):
            el = soup.select_one(sel.strip())
            if el:
                img = el.get("content") or el.get("src") or el.get("href") or el.get("data-src")
                if img:
                    norm = _normalise_img_url(img, base_domain)
                    if norm and not _is_share_image(norm):
                        return norm
    
    # 3. JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if isinstance(d, dict) and d.get("@type") in ("Product", "Offer"):
                img = d.get("image")
                if isinstance(img, list) and img:
                    norm = _normalise_img_url(str(img[0]), base_domain)
                    if norm and not _is_share_image(norm):
                        return norm
                if isinstance(img, str):
                    norm = _normalise_img_url(img, base_domain)
                    if norm and not _is_share_image(norm):
                        return norm
    
    # 4. Fallback (og:image)
    meta = soup.select_one("meta[property='og:image']")
    if meta:
        norm = _normalise_img_url(meta.get("content"), base_domain)
        if norm and not _is_share_image(norm):
            return norm
        
    return None

def _extract_all_images(soup: BeautifulSoup, config: SiteConfig, base_url: str) -> List[str]:
    """
    Extracts all unique product images found via selectors, JSON-LD, or scripts.
    """
    images: List[str] = []
    base_domain = _origin_from_url(base_url).replace("https://", "").replace("http://", "")

    # 1. Config selector (Primary source for galleries)
    if config.image_selector:
        for sel in config.image_selector.split(","):
            for el in soup.select(sel.strip()):
                img = el.get("content") or el.get("src") or el.get("href") or el.get("data-src")
                if img:
                    images.append(img)

    # 2. Try JS "var meta" (Shopify - often has "images" array)
    for s in soup.find_all("script"):
        if s.string and "var meta =" in s.string:
            # Look for "images": [...]
            m = re.search(r'"images"\s*:\s*(\[.*?\])', s.string, re.DOTALL)
            if m:
                try:
                    js_imgs = json.loads(m.group(1))
                    for i in js_imgs:
                        if isinstance(i, str):
                            images.append(i)
                except Exception:
                    pass
            # Also try single ImageURL if list failed or empty
            if not images:
                 m_single = re.search(r'ImageURL\s*:\s*"([^"]+)"', s.string)
                 if m_single:
                     images.append(m_single.group(1))

    # 3. JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if isinstance(d, dict) and d.get("@type") in ("Product", "Offer"):
                img = d.get("image")
                if isinstance(img, list):
                    for i in img:
                        images.append(str(i))
                elif isinstance(img, str):
                    images.append(img)

    # 4. Fallback (og:image) - usually just one, but add it just in case
    meta = soup.select_one("meta[property='og:image']")
    if meta and meta.get("content"):
        images.append(meta.get("content"))

    # Normalise and Deduplicate
    seen = set()
    final = []
    for raw in images:
        norm = _normalise_img_url(raw, base_domain)
        if norm and not _is_share_image(norm):
            # Deduplicate based on URL without query params (e.g. ignore width/height/v)
            dedupe_key = norm.split('?')[0]
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                final.append(norm)
    
    return final

def _extract_name(soup: BeautifulSoup, config: SiteConfig) -> Optional[str]:
    # 1. Try JS "var item"
    for s in soup.find_all("script"):
        if s.string and "var item" in s.string:
            m = re.search(r'Name\s*:\s*"([^"]+)"', s.string)
            if m:
                return m.group(1).strip()

    # 2. Config selector
    if config.name_selector:
        for sel in config.name_selector.split(","):
            el = soup.select_one(sel.strip())
            if el:
                txt = el.get("content") or el.get_text(strip=True)
                if txt:
                    return txt
    
    # 3. Fallback
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    
    url_like = _doc_url(soup)
    return _slug_to_name(url_like)

def _extract_breadcrumbs(soup: BeautifulSoup, forced_selector: Optional[str]) -> List[str]:
    # 1. Try JS "var item" (Categories)
    for s in soup.find_all("script"):
        if s.string and "var item" in s.string:
            # Pattern: Categories: ["All","Boots",...]
            m = re.search(r'Categories\s*:\s*\[(.*?)\]', s.string, re.DOTALL)
            if m:
                # Extract list content
                content = m.group(1)
                # Parse strings from the list content (e.g. "All", "Boots")
                # We can use regex to find all quoted strings
                cats = re.findall(r'"([^"]+)"', content)
                if cats:
                    return [c.strip() for c in cats if c.strip()]

    # JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        datasets = data if isinstance(data, list) else [data]
        for d in datasets:
            if isinstance(d, dict) and d.get("@type") == "BreadcrumbList":
                items = d.get("itemListElement") or []
                names = []
                for it in items:
                    if isinstance(it, dict):
                        nm = it.get("name")
                        if not nm and isinstance(it.get("item"), dict):
                            nm = it["item"].get("name")
                        if nm:
                            names.append(str(nm).strip())
                if names: return names

    # Microdata
    names = []
    for li in soup.select('[itemprop="itemListElement"][itemscope][itemtype*="ListItem"]'):
        a = li.select_one('[itemprop="item"]')
        txt = a.get_text(strip=True) if a else None
        if txt: names.append(txt)
    if names: return names

    # Selectors
    if forced_selector:
        links = [a.get_text(strip=True) for a in soup.select(forced_selector + " a")]
        if links: return links
    
    for sel in ["nav.breadcrumb a", ".breadcrumb a", ".woocommerce-breadcrumb a",
                "ol.breadcrumb li a", "ul.breadcrumb li a", "nav[aria-label='breadcrumb'] a"]:
        links = [a.get_text(strip=True) for a in soup.select(sel)]
        if links: return links
    return []

def _extract_sku(soup: BeautifulSoup, url: str | None, config: Optional[SiteConfig] = None) -> Optional[str]:
    # JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            if isinstance(d, dict) and d.get("@type") == "Product":
                sku = d.get("sku")
                if isinstance(sku, (str, int)) and str(sku).strip():
                    return str(sku).strip()

    # Config selector
    if config and config.sku_selector:
        for sel in config.sku_selector.split(","):
            el = soup.select_one(sel.strip())
            if el:
                txt = el.get_text(strip=True) if hasattr(el, "get_text") else el.get("content")
                if txt:
                    # Clean up "SKU: 123" -> "123"
                    m = re.search(r"(?:SKU\s*[:#-]?\s*)?([A-Za-z0-9._-]{3,})", txt, re.I)
                    if m: return m.group(1).strip()
                    return txt.strip()
        
        # JS Pattern
        if config.sku_js_pattern:
            for s in soup.find_all("script"):
                if not s.string: continue
                m = re.search(config.sku_js_pattern, s.string, re.DOTALL | re.IGNORECASE)
                if m: return m.group(1).strip()
    
    # Selectors
    el = soup.select_one("[itemprop='sku']") or soup.select_one("meta[itemprop='sku']")
    if el:
        txt = el.get_text(strip=True) if hasattr(el, "get_text") else el.get("content")
        if txt: return txt.strip()
        
    for sel in [".product-sku", "#sku", ".sku"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                m = re.search(r"SKU\s*[:#-]?\s*([A-Za-z0-9._-]{3,})", t, re.I)
                if m: return m.group(1).strip()
    
    # URL
    if url:
        m = re.search(r"/p/([^/?#]+)/?", url)
        if m: return m.group(1)
    
    # JS Fallback
    for s in soup.find_all("script"):
        if not s.string: continue
        if "ShopifyAnalytics" in s.string or "var meta =" in s.string:
            m = re.search(r'"sku"\s*:\s*"([^"]+)"', s.string)
            if m: return m.group(1)
    return None

def _extract_all_skus(soup: BeautifulSoup) -> List[str]:
    skus = set()
    
    # 1. JS "var meta" (variants)
    for s in soup.find_all("script"):
        if s.string and "var meta =" in s.string:
            # Extract variants array
            m = re.search(r'"variants"\s*:\s*(\[.*?\])', s.string, re.DOTALL)
            if m:
                try:
                    variants = json.loads(m.group(1))
                    for v in variants:
                        if v.get("sku"):
                            skus.add(str(v["sku"]).strip())
                except Exception:
                    pass
        
        # 2. ShopifyAnalytics
        if s.string and "ShopifyAnalytics" in s.string:
            # Look for "sku": "..."
            for m in re.finditer(r'"sku"\s*:\s*"([^"]+)"', s.string):
                skus.add(m.group(1).strip())

    # 3. JSON-LD
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            if isinstance(d, dict):
                if d.get("@type") == "Product":
                    sku = d.get("sku")
                    if sku: skus.add(str(sku).strip())
                    # Check offers
                    offers = d.get("offers")
                    if isinstance(offers, list):
                        for o in offers:
                            if o.get("sku"): skus.add(str(o["sku"]).strip())
    
    # 4. Visible SKU
    visible = _extract_sku(soup, None, None)
    if visible:
        skus.add(visible)

    return list(skus)

def _extract_shopify_ids(soup: BeautifulSoup, target_sku: Optional[str]) -> tuple[Optional[str], Optional[str], List[str]]:
    """
    Extracts (Group ID, Variant ID, All Variant IDs) from Shopify sources.
    """
    group_id = None
    variant_id = None
    all_variant_ids = []

    # 1. Try "data-events" (Newer Shopify themes / pixels)
    # <script ... data-events="[[...]]">
    for s in soup.find_all("script", attrs={"data-events": True}):
        try:
            raw = s.get("data-events")
            if not raw: continue
            # It's usually a JSON string inside the attribute
            events = json.loads(raw)
            for evt in events:
                # evt is [name, data]
                if len(evt) >= 2 and evt[0] == "product_viewed":
                    data = evt[1]
                    # data has "productVariant" -> "product" -> "id" (Group ID)
                    # and "productVariant" -> "id" (Variant ID)
                    pv = data.get("productVariant") or {}
                    prod = pv.get("product") or {}
                    
                    # Group ID
                    if prod.get("id"):
                        group_id = str(prod["id"])
                    
                    # Variant ID (Current)
                    if pv.get("id"):
                        # If we have a target SKU, check if this is the one?
                        # Actually product_viewed usually refers to the *current* variant being viewed.
                        # So if we are on that page, this is likely the variant ID.
                        # But let's verify SKU if possible.
                        v_sku = str(pv.get("sku") or "").strip().lower()
                        t_sku = str(target_sku).strip().lower() if target_sku else ""
                        
                        # If target_sku is provided, we only set variant_id if it matches OR if we assume the page is for this SKU.
                        # But "product_viewed" is for the specific variant on page load.
                        if not target_sku or (v_sku == t_sku) or (not variant_id):
                             variant_id = str(pv["id"])

                    # All Variant IDs?
                    # "product_viewed" usually only contains the current variant.
                    # We might not get all variants here.
                    if variant_id and variant_id not in all_variant_ids:
                        all_variant_ids.append(variant_id)
        except Exception:
            pass
    
    # 2. Try "var meta" (Standard Shopify)
    if not group_id:
        for s in soup.find_all("script"):
            if s.string and "var meta =" in s.string:
                # Extract product ID (Group ID)
                m_prod = re.search(r'"product"\s*:\s*\{[^}]*"id"\s*:\s*(\d+)', s.string)
                if m_prod:
                    group_id = m_prod.group(1)

                # Extract Variants
                m_vars = re.search(r'"variants"\s*:\s*(\[.*?\])', s.string, re.DOTALL)
                if m_vars:
                    try:
                        variants = json.loads(m_vars.group(1))
                        for v in variants:
                            vid = str(v.get("id"))
                            if vid not in all_variant_ids:
                                all_variant_ids.append(vid)
                                
                            v_sku = str(v.get("sku") or "").strip().lower()
                            t_sku = str(target_sku).strip().lower() if target_sku else ""
                            
                            if t_sku and v_sku == t_sku:
                                variant_id = vid
                    except Exception:
                        pass
                break

    # 3. Fallback: JSON-LD
    if not group_id or (target_sku and not variant_id):
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string or "")
            except Exception:
                continue
            docs = data if isinstance(data, list) else [data]
            for d in docs:
                if isinstance(d, dict) and d.get("@type") == "Product":
                    if not group_id:
                        gid = d.get("productID")
                        if gid: group_id = str(gid)
                    
                    # Variants
                    offers = d.get("offers") or d.get("hasVariant")
                    if isinstance(offers, list):
                        for o in offers:
                            # Try to find ID? JSON-LD often lacks explicit variant IDs
                            pass

    return group_id, variant_id, all_variant_ids

def _extract_discount_badge(soup: BeautifulSoup, config: Optional[SiteConfig] = None) -> Optional[float]:
    """
    Extracts discount from badges like "50% OFF".
    """
    # 0. Check config selectors first (AVOID CLONING SOUP IF FOUND)
    if config and config.discount_selector:
        for sel in config.discount_selector.split(","):
            badge = soup.select_one(sel.strip())
            if badge:
                text = badge.get_text(strip=True)
                m = re.search(r'(\d+(?:\.\d+)?)%', text)
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
    
    # 1. Fallback: Look for specific badge class names (requires cleanup to avoid false positives)
    # Clone soup only now
    soup_copy = BeautifulSoup(str(soup), "lxml")
    for nav in soup_copy.find_all(['nav', 'breadcrumb']):
        nav.decompose()
    for elem in soup_copy.find_all(class_=re.compile(r'breadcrumb|navigation', re.I)):
        elem.decompose()
    
    for class_name in ["c-badge__item--percentage-off", "percentage-off", "discount-badge", "sale-badge", "badge--sale", "product-badge"]:
        badge = soup_copy.find(class_=re.compile(class_name, re.I))
        if badge:
            text = badge.get_text(strip=True)
            # Match "50% OFF" or "-50%"
            m = re.search(r'(\d+(?:\.\d+)?)%', text)
            if m:
                try:
                    return float(m.group(1))
                except ValueError:
                    pass
    
    # 2. Look for text containing "% OFF" in product area only
    # Limit search to main/product containers
    product_area = soup_copy.find(['main', 'article']) or soup_copy.find(class_=re.compile(r'product|item', re.I)) or soup_copy
    
    for elem in product_area.find_all(string=re.compile(r'\d+%\s*OFF', re.I)):
        m = re.search(r'(\d+(?:\.\d+)?)%', elem)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass

    return None

def parse_product(html: str, url: str, config: SiteConfig, sku: Optional[str] = None) -> dict:
    try:
        soup = BeautifulSoup(html, "lxml")
        if not sku:
            sku = _extract_sku(soup, url, config) or None

        name = _extract_name(soup, config)
        breadcrumbs = _extract_breadcrumbs(soup, getattr(config, "breadcrumbs_selector", None))
        category = breadcrumbs[-2] if len(breadcrumbs) >= 2 else (breadcrumbs[-1] if breadcrumbs else None)

        price = _extract_price(soup, config)
        rrp = _extract_rrp(soup, config)
        rrp = _extract_rrp(soup, config)
        
        # Multi-image extraction
        all_images = _extract_all_images(soup, config, url)
        image_url = all_images[0] if all_images else None
        
        all_skus = _extract_all_skus(soup)
        
        # Extract IDs
        group_id, variant_id, all_variant_ids = _extract_shopify_ids(soup, sku)

        discount = None
        # Try badge extraction first (more accurate)
        discount = _extract_discount_badge(soup, config)
        
        # If badge extraction failed, try calculating from price/rrp
        if discount is None and price is not None and rrp is not None and rrp > 0:
            discount = round((1 - (price / rrp)) * 100, 2)

        result = {
            "sku": sku,
            "group_id": group_id,
            "variant_id": variant_id,
            "all_variant_ids": all_variant_ids,
            "all_skus": all_skus,
            "url": None,
            "product_url": url,
            "name": name,
            "category": category,
            "breadcrumbs": " > ".join(breadcrumbs) if breadcrumbs else None,
            "price": price,
            "rrp": rrp,
            "discount_percent": discount,
            "image_url": image_url,
            "error": None,
        }
        
        # Add secondary images
        if all_images and len(all_images) > 1:
            for i, img in enumerate(all_images[1:5], start=2): # Limit to 5 images total
                result[f"image_url_{i}"] = img
            
        return result
    except Exception as e:
        return {
            "sku": sku,
            "group_id": None,
            "variant_id": None,
            "all_variant_ids": [],
            "all_skus": [],
            "url": None,
            "product_url": url,
            "name": None,
            "category": None,
            "breadcrumbs": None,
            "price": None,
            "rrp": None,
            "discount_percent": None,
            "image_url": None,
            "error": str(e),
        }
