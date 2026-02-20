
from __future__ import annotations
from bs4 import BeautifulSoup
from .parser import parse_product
from .config import SITE_CONFIGS

def looks_like_neto(html_or_soup) -> bool:
    soup = BeautifulSoup(html_or_soup, "lxml") if isinstance(html_or_soup, str) else html_or_soup
    gen = soup.select_one('meta[name="generator"]')
    if gen and isinstance(gen.get("content"), str) and "neto" in gen.get("content").lower():
        return True
    if soup.select_one(".productpricetext") or soup.select_one(".productrrp"):
        return True
    return False

def parse_auto(html: str, url: str, sku: str | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    if looks_like_neto(soup):
        cfg = SITE_CONFIGS.get("neto_default")
        if cfg:
            return parse_product(html, url, cfg, sku=sku)
    cfg = SITE_CONFIGS.get("neto_default")
    return parse_product(html, url, cfg, sku=sku) if cfg else {
        "sku": sku, "url": None, "product_url": url, "name": None,
        "category": None, "breadcrumbs": None, "price": None, "rrp": None,
        "discount_percent": None, "image_url": None, "error": "No parser available"
    }
