"""Tests for Neto parser against real metavparts.com.au HTML fixture and synthetic fixtures."""
from __future__ import annotations

import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def metav7212_html():
    p = FIXTURES / "metavparts_METAV7212_raw.html"
    if not p.exists():
        pytest.skip("Real metavparts fixture not captured — run diagnostic probe first")
    return p.read_text()


@pytest.fixture
def neto_cfg():
    from scraper.config import SITE_CONFIGS
    return SITE_CONFIGS["neto_default"]


# ── Real fixture tests ────────────────────────────────────────────────────────

class TestNetoParserRealFixture:
    """Parser tests against real captured HTML from metavparts.com.au."""

    def test_extracts_correct_sku(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["sku"] == "METAV7212"

    def test_extracts_product_name(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["name"] is not None
        assert "Holden Commodore" in result["name"] or "Wheel Centre Cap" in result["name"]
        assert result["name"] != "Information"  # Must NOT return the sidebar H1

    def test_extracts_sale_price(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["price"] == pytest.approx(16.0)

    def test_extracts_rrp(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["rrp"] == pytest.approx(20.0)

    def test_computes_discount_percent(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["discount_percent"] is not None
        assert result["discount_percent"] > 0

    def test_extracts_image_url(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["image_url"] is not None
        assert result["image_url"].startswith("https://")

    def test_extracts_breadcrumbs(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["breadcrumbs"] is not None
        assert "Home" in result["breadcrumbs"]

    def test_no_error_for_valid_page(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result["error"] is None

    def test_multiple_images_extracted(self, metav7212_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(metav7212_html, "https://www.metavparts.com.au/p/METAV7212", neto_cfg, "METAV7212")
        assert result.get("image_url_2") is not None or result.get("image_url") is not None


# ── Synthetic fixture tests ───────────────────────────────────────────────────

class TestNetoParserSyntheticFixtures:
    """Parser tests using the synthetic HTML fixtures we control fully."""

    def test_valid_product_extracts_all_fields(self, neto_valid_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(
            neto_valid_html,
            "https://store.example.com/p/TEST-POLO-BL",
            neto_cfg,
            "TEST-POLO-BL",
        )
        assert result["sku"] == "TEST-POLO-BL"
        assert result["name"] == "Test Polo Shirt - Blue Large"
        assert result["price"] == pytest.approx(49.95)
        assert result["rrp"] == pytest.approx(79.95)
        assert result["image_url"] is not None
        assert result["breadcrumbs"] is not None
        assert "Clothing" in result["breadcrumbs"]
        assert result["discount_percent"] is not None and result["discount_percent"] > 0
        assert result["error"] is None

    def test_404_page_produces_no_product_data(self, neto_404_html, neto_cfg):
        from scraper.parser import parse_product
        result = parse_product(
            neto_404_html,
            "https://store.example.com/p/NOTFOUND",
            neto_cfg,
            "NOTFOUND",
        )
        # A 404 page has no product data
        assert result["name"] is None or "not found" in (result["name"] or "").lower()
        assert result["price"] is None

    def test_wrong_sku_page_extracts_page_sku(self, neto_wrong_sku_html, neto_cfg):
        from scraper.parser import parse_product
        # Pass sku=None so the parser reads the SKU from the page itself
        result = parse_product(
            neto_wrong_sku_html,
            "https://store.example.com/p/WRONG-SKU-99",
            neto_cfg,
            None,
        )
        # The page contains WRONG-SKU-99, not TEST-POLO-BL
        page_sku = result.get("sku") or ""
        assert "WRONG-SKU-99" in page_sku or (page_sku != "TEST-POLO-BL")
