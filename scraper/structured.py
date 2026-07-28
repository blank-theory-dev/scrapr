"""Site-agnostic product extraction from a page's structured data.

Most carts publish their product record in machine-readable form. Reading that
is far more durable than guessing CSS classes, and it needs no per-site config:

  1. schema.org Product JSON-LD   — machter.com.au, most modern carts
  2. embedded JS config object    — Neto 'k4n' (metavparts' theme)
  3. microdata (itemprop=...)     — schema.org expressed in markup
  4. OpenGraph / twitter meta     — near-universal, thin

Each layer only fills fields still missing, so earlier (richer) sources win.

parse_product() uses this to fill gaps its CSS selectors left behind, which is
deliberately conservative: on a tuned site nothing changes, and on an untuned
one the page's own metadata rescues the row instead of returning blanks.

Price is the one field to treat with suspicion — Neto's k4n `price` is the
*base* price, not the promo price, so a tuned sale selector must outrank it.

No new dependencies: bs4 + stdlib only.
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote, urljoin

FIELDS = ("sku", "name", "price", "rrp", "image_url", "brand", "category", "product_url")


def _num(v):
    if v is None:
        return None
    m = re.search(r"[\d]+(?:[.,]\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _merge(dst: dict, src: dict) -> None:
    """Fill only fields that are still empty — earlier layers win."""
    for k, v in src.items():
        if v not in (None, "", []) and not dst.get(k):
            dst[k] = v


# ── layer 1: JSON-LD ─────────────────────────────────────────────────────────
def from_jsonld(soup, base_url: str) -> dict:
    out: dict = {}
    for sc in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        try:
            data = json.loads(sc.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
                continue
            if not isinstance(d, dict):
                continue
            if "@graph" in d:
                stack.extend(d["@graph"] if isinstance(d["@graph"], list) else [d["@graph"]])
            types = d.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" not in types:
                continue
            offers = d.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            brand = d.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            img = d.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            _merge(out, {
                "sku": d.get("sku") or d.get("mpn"),
                "name": d.get("name"),
                "price": _num(offers.get("price")),
                "rrp": _num(offers.get("highPrice")),
                "image_url": urljoin(base_url, img) if img else None,
                "brand": brand,
                "product_url": offers.get("url") or d.get("url"),
            })
    return out


# ── layer 2: embedded JS config objects (Neto k4n, and friends) ──────────────
_JS_OBJECTS = (
    r"k4n\s*=\s*(\{.*?\})\s*;",          # Neto
    r"var\s+meta\s*=\s*(\{.*?\})\s*;",   # Shopify theme meta
)


def _js_field(blob: str, key: str):
    m = re.search(rf"{key}\s*:\s*[\"']([^\"']*)[\"']", blob)
    return unquote(m.group(1)) if m and m.group(1) else None


def from_js_object(html: str, base_url: str) -> dict:
    out: dict = {}
    for pat in _JS_OBJECTS:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        blob = m.group(1)
        cats = None
        cm = re.search(r"categories\s*:\s*\[(.*?)\]", blob, re.S)
        if cm:
            names = [unquote(x) for x in re.findall(r"[\"']([^\"']+)[\"']", cm.group(1))]
            cats = names[-1] if names else None
        img = _js_field(blob, "image")
        _merge(out, {
            "sku": _js_field(blob, "sku"),
            "name": _js_field(blob, "name"),
            "price": _num(_js_field(blob, "price")),
            "rrp": _num(_js_field(blob, "rrp")),
            "image_url": urljoin(base_url, img) if img else None,
            "brand": _js_field(blob, "brand"),
            "category": cats,
            "product_url": _js_field(blob, "url"),
        })
    return out


# ── layer 3: microdata ───────────────────────────────────────────────────────
def _prop(soup, name: str):
    el = soup.select_one(f'[itemprop="{name}"]')
    if not el:
        return None
    return (el.get("content") or el.get("href") or el.get("src")
            or el.get_text(" ", strip=True) or None)


def from_microdata(soup, base_url: str) -> dict:
    img = _prop(soup, "image")
    return {k: v for k, v in {
        "sku": _prop(soup, "sku") or _prop(soup, "productID"),
        "name": _prop(soup, "name"),
        "price": _num(_prop(soup, "price")),
        "image_url": urljoin(base_url, img) if img else None,
        "brand": _prop(soup, "brand"),
    }.items() if v}


# ── layer 4: OpenGraph ───────────────────────────────────────────────────────
def _meta(soup, *names):
    for n in names:
        el = soup.select_one(f'meta[property="{n}"]') or soup.select_one(f'meta[name="{n}"]')
        if el and el.get("content"):
            return el["content"]
    return None


def from_opengraph(soup, base_url: str) -> dict:
    img = _meta(soup, "og:image", "twitter:image")
    return {k: v for k, v in {
        "name": _meta(soup, "og:title", "twitter:title"),
        "price": _num(_meta(soup, "og:price:amount", "product:price:amount")),
        "image_url": urljoin(base_url, img) if img else None,
        "product_url": _meta(soup, "og:url"),
    }.items() if v}


def extract(html: str, base_url: str, soup) -> tuple[dict, list[str]]:
    """Return (data, layers_that_contributed)."""
    data: dict = {}
    used: list[str] = []
    for label, fn in (
        ("jsonld", lambda: from_jsonld(soup, base_url)),
        ("js_object", lambda: from_js_object(html, base_url)),
        ("microdata", lambda: from_microdata(soup, base_url)),
        ("opengraph", lambda: from_opengraph(soup, base_url)),
    ):
        before = sum(1 for f in FIELDS if data.get(f))
        _merge(data, fn())
        if sum(1 for f in FIELDS if data.get(f)) > before:
            used.append(label)
    return data, used
