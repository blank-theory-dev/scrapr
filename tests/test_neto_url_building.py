"""Tests for Neto URL construction — pipeline._build_url_for_sku."""
from __future__ import annotations

from scraper.pipeline import _build_url_for_sku


ORIGIN = "https://www.metavparts.com.au"


class TestNetoUrlBuilding:
    def test_sku_builds_slash_p_url(self):
        url = _build_url_for_sku("METAV575", None, "Neto", ORIGIN, None)
        assert url == f"{ORIGIN}/p/METAV575"

    def test_explicit_url_takes_precedence_over_sku(self):
        explicit = f"{ORIGIN}/product/some-slug"
        url = _build_url_for_sku("METAV575", explicit, "Neto", ORIGIN, None)
        assert url == explicit

    def test_no_origin_returns_none(self):
        url = _build_url_for_sku("METAV575", None, "Neto", None, None)
        assert url is None

    def test_no_sku_no_url_returns_none(self):
        url = _build_url_for_sku(None, None, "Neto", ORIGIN, None)
        assert url is None

    def test_url_pattern_overrides_default_neto_url(self):
        url = _build_url_for_sku("METAV575", None, "Neto", ORIGIN, f"{ORIGIN}/buy/{{sku}}")
        assert url == f"{ORIGIN}/buy/METAV575"

    def test_shopify_without_pattern_falls_back_to_search(self):
        url = _build_url_for_sku("72787", None, "Shopify", "https://legear.com.au", None)
        assert url == "https://legear.com.au/search?type=product&q=72787"

    def test_woocommerce_without_pattern_returns_none(self):
        url = _build_url_for_sku("ABC123", None, "WordPress (WooCommerce)", "https://shop.example.com", None)
        assert url is None

    def test_neto_sku_with_hyphens_and_uppercase(self):
        url = _build_url_for_sku("MJL-BLK-NSXX3", None, "Neto", ORIGIN, None)
        assert url == f"{ORIGIN}/p/MJL-BLK-NSXX3"

    def test_neto_sku_with_mixed_case(self):
        url = _build_url_for_sku("Pantry-300BLK", None, "Neto", ORIGIN, None)
        assert url == f"{ORIGIN}/p/Pantry-300BLK"
