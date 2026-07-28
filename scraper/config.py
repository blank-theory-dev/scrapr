
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Pattern
import re


@dataclass
class SiteConfig:
    # Ordered fallback URL path templates for SKU-based URL construction, e.g.
    # "/p/{sku}". The pipeline tries them in order, stopping on the first HTTP 200.
    url_patterns: List[str] = field(default_factory=list)
    price_selector: Optional[str] = None
    sale_price_selector: Optional[str] = None
    rrp_selector: Optional[str] = None
    image_selector: Optional[str] = None
    discount_selector: Optional[str] = None
    name_selector: Optional[str] = None
    # Category is taken from the breadcrumb trail, so there is no separate selector.
    breadcrumbs_selector: Optional[str] = None
    sku_selector: Optional[str] = None
    sku_js_pattern: Optional[str] = None
    price_js_pattern: Optional[str] = None
    sale_price_js_pattern: Optional[str] = None
    price_regex: Pattern = re.compile(r"[\d\.,]+")


SITE_CONFIGS: Dict[str, SiteConfig] = {
    "neto_default": SiteConfig(
        # /buy/{sku} was a guess at an alternate Neto theme route; it 404s on every
        # store tested, so it only bought a wasted request per dead SKU.
        url_patterns=["/p/{sku}"],
        sku_selector="[itemprop='sku'], [itemprop='productID'], .sku, .product-sku, span[itemprop='sku']",
        sku_js_pattern=r"k4n\s*=\s*\{.*?sku\s*:\s*[\"']([^\"']+)[\"']",
        price_js_pattern=r"k4n\s*=\s*\{.*?price\s*:\s*[\"']([\d\.,]+)[\"']",
        price_selector=(
            ".h1[itemprop='price'], [itemprop='price'], "
            ".productpricetext, .price .amount, .summary .price, "
            ".woocommerce-Price-amount"
        ),
        sale_price_selector=(
            ".productpromo, .productsaleprice, .sale-price, .price--sale, .special-price, .price-now"
        ),
        rrp_selector=(
            ".productwasprice, .productrrp, .rrp, .was-price, .price .compare, .compare-at"
        ),
        image_selector=(
            "a[data-lightbox='product-lightbox'], "
            ".product-image a, #main-image a, .productView-image a, "
            "img[itemprop='image'], #main-image img, .productView-image img, "
            ".product-image img, .woocommerce-product-gallery__image img, .product-gallery img, "
            ".product-thumbnails img, .product-thumbnails a, "
            ".owl-item .thumbnail-image, .thumb-image, "
            ".embed-responsive-item img, .product-image-small, "
            "meta[property='og:image'], meta[name='twitter:image'], link[rel='image_src']"
        ),
        discount_selector=(
            ".productsave, .mm_off, .badge--sale, .product__badge--save"
        ),
        name_selector=(
            "h1[itemprop='name'], .product-title, .product_title, "
            "meta[property='og:title'], meta[name='twitter:title']"
        ),
        breadcrumbs_selector=(
            "[itemprop='itemListElement'] [itemprop='item'], "
            "nav.breadcrumb a, .breadcrumb a, .woocommerce-breadcrumb a, "
            "ol.breadcrumb li a, ul.breadcrumb li a, nav[aria-label='breadcrumb'] a"
        ),
    ),
    "shopify_default": SiteConfig(
        price_selector=(
            "meta[property='og:price:amount'], meta[property='product:price:amount'], "
            ".price-item--sale, .price-item--regular, .product__price, .price .amount, "
            "#ProductPrice-product-template, .product-single__price, .current_price"
        ),
        sale_price_selector=(
            ".price-item--sale, .product__price--sale, .sale-price, .special-price, .price--on-sale .price-item--sale"
        ),
        rrp_selector=(
            ".price__compare, .price--compare, .compare-at, .product-single__price--compare-at, "
            "s.price-item--regular, .old-price, .was_price"
        ),
        image_selector=(
            "meta[property='og:image'], meta[name='twitter:image'], "
            "img[src*='/products/'][data-src], img[src*='/cdn/shop/products/'], "
            "img[data-gallery='gallery'], "
            ".product-single__photo img, .product__media img, "
            ".c-product-main__media img, .swiper-slide img, "
            ".c-product-main__info-thumbnails__thumbnail img, .u-object-image"
        ),
        discount_selector=(
            ".badge--sale, .price__badge-sale, .product-label--sale, "
            ".sale-label, .product-tag--sale"
        ),
        name_selector=(
            "h1.product__title, h1.product-single__title, h1.title, "
            "meta[property='og:title'], meta[name='twitter:title']"
        ),
        breadcrumbs_selector=("nav.breadcrumb a, .breadcrumb a, .breadcrumbs a"),
    ),
    "wordpress_default": SiteConfig(
        price_selector=(".summary .price, .woocommerce-Price-amount"),
        sale_price_selector=(".price ins .amount, .price .woocommerce-Price-currencySymbol + ins .amount, .sale-price"),
        rrp_selector=(".price del .amount, .price .woocommerce-Price-currencySymbol + del .amount"),
        image_selector=("meta[property='og:image'], img.wp-post-image, .woocommerce-product-gallery__image img"),
        discount_selector=(".onsale, .badge--sale"),
        name_selector=("h1.product_title, [itemprop='name'], meta[property='og:title']"),
        breadcrumbs_selector=(".woocommerce-breadcrumb a, nav.breadcrumb a, .breadcrumb a"),
    ),
}
