
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Pattern, Optional
import re

@dataclass
class SiteConfig:
    base_domain: str
    url_pattern: Optional[str] = None
    price_selector: Optional[str] = None
    rrp_selector: Optional[str] = None
    image_selector: Optional[str] = None
    discount_selector: Optional[str] = None
    name_selector: Optional[str] = None
    category_selector: Optional[str] = None
    breadcrumbs_selector: Optional[str] = None
    sku_selector: Optional[str] = None
    sku_js_pattern: Optional[str] = None
    price_js_pattern: Optional[str] = None
    price_regex: Pattern = re.compile(r"[\d\.,]+")
    rrp_regex: Pattern = re.compile(r"[\d\.,]+")

SITE_CONFIGS: Dict[str, SiteConfig] = {
    "neto_default": SiteConfig(
        base_domain="neto.generic",
        sku_selector="[itemprop='sku'], .sku, .product-sku, span[itemprop='sku']",
        sku_js_pattern=r"k4n\s*=\s*\{.*?sku\s*:\s*[\"']([^\"']+)[\"']",
        price_js_pattern=r"k4n\s*=\s*\{.*?price\s*:\s*[\"']([\d\.,]+)[\"']",
        price_selector=(
            ".h1[itemprop='price'], [itemprop='price'], "
            ".productpricetext, .price .amount, .summary .price, "
            ".woocommerce-Price-amount"
        ),
        rrp_selector=(
            ".productrrp, .rrp, .was-price, .price .compare, .compare-at"
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
        category_selector=(
            "[itemprop='itemListElement'] [itemprop='item'], "
            "nav.breadcrumb a, .breadcrumb a, .woocommerce-breadcrumb a, "
            "ol.breadcrumb li a, ul.breadcrumb li a, nav[aria-label='breadcrumb'] a"
        ),
        breadcrumbs_selector=(
            "[itemprop='itemListElement'] [itemprop='item'], "
            "nav.breadcrumb a, .breadcrumb a, .woocommerce-breadcrumb a, "
            "ol.breadcrumb li a, ul.breadcrumb li a, nav[aria-label='breadcrumb'] a"
        ),
    ),
    "shopify_default": SiteConfig(
        base_domain="shopify.generic",
        # Price: meta tags, common classes, JSON-LD fallback handled in parser
        price_selector=(
            "meta[property='og:price:amount'], meta[property='product:price:amount'], "
            ".price-item--sale, .price-item--regular, .product__price, .price .amount, "
            "#ProductPrice-product-template, .product-single__price, .current_price"
        ),
        # RRP: compare-at prices, strikethrough elements
        rrp_selector=(
            ".price__compare, .price--compare, .compare-at, .product-single__price--compare-at, "
            "s.price-item--regular, .old-price, .was_price"
        ),
        # Image: OpenGraph, specific Shopify CDN patterns, common gallery classes
        image_selector=(
            "meta[property='og:image'], meta[name='twitter:image'], "
            "img[src*='/products/'][data-src], img[src*='/cdn/shop/products/'], "
            "img[data-gallery='gallery'], "
            ".product-single__photo img, .product__media img, "
            ".c-product-main__media img, .swiper-slide img, "
            ".c-product-main__info-thumbnails__thumbnail img, .u-object-image"
        ),
        # Discount: sale badges, saved amount text
        discount_selector=(
            ".badge--sale, .price__badge-sale, .product-label--sale, "
            ".sale-label, .product-tag--sale"
        ),
        # Name: H1, meta tags
        name_selector=(
            "h1.product__title, h1.product-single__title, h1.title, "
            "meta[property='og:title'], meta[name='twitter:title']"
        ),
        # Category: Breadcrumbs usually best source
        category_selector=("nav.breadcrumb a, .breadcrumb a, .breadcrumbs a"),
        breadcrumbs_selector=("nav.breadcrumb a, .breadcrumb a, .breadcrumbs a"),
    ),
    "wordpress_default": SiteConfig(
        base_domain="woo.generic",
        price_selector=(".summary .price, .woocommerce-Price-amount"),
        rrp_selector=(".price del .amount, .price .woocommerce-Price-currencySymbol + del .amount"),
        image_selector=("meta[property='og:image'], img.wp-post-image, .woocommerce-product-gallery__image img"),
        discount_selector=(".onsale, .badge--sale"),
        name_selector=("h1.product_title, [itemprop='name'], meta[property='og:title']"),
        category_selector=(".woocommerce-breadcrumb a, nav.breadcrumb a, .breadcrumb a"),
        breadcrumbs_selector=(".woocommerce-breadcrumb a, nav.breadcrumb a, .breadcrumb a"),
    ),
}
