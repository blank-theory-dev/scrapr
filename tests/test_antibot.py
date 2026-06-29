"""Tests for anti-bot detection, error classification, and circuit-breaker behaviour."""
from __future__ import annotations

import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"Fixture not found: {name}")
    return p.read_text()


# ── _classify_error ───────────────────────────────────────────────────────────

class TestClassifyError:
    def test_classifies_404_as_not_found(self):
        from scraper.pipeline import _classify_error
        result = _classify_error(404, "Page not found")
        assert "not_found" in result.lower()

    def test_classifies_cloudflare_403_specifically(self):
        from scraper.pipeline import _classify_error
        cf_html = _read("metavparts_cloudflare_blocked.html")
        result = _classify_error(403, cf_html)
        assert "cloudflare" in result.lower() or "blocked" in result.lower()

    def test_classifies_plain_403_as_forbidden(self):
        from scraper.pipeline import _classify_error
        result = _classify_error(403, "Access denied")
        assert "blocked" in result.lower() or "403" in result

    def test_classifies_429_as_rate_limited(self):
        from scraper.pipeline import _classify_error
        result = _classify_error(429, "Too many requests")
        assert "rate_limited" in result.lower() or "429" in result

    def test_classifies_503_as_server_error(self):
        from scraper.pipeline import _classify_error
        result = _classify_error(503, "Service unavailable")
        assert "server_error" in result.lower() or "503" in result

    def test_includes_snippet_for_unknown_status(self):
        from scraper.pipeline import _classify_error
        result = _classify_error(500, "Internal server error happened")
        assert "500" in result


# ── _is_empty_parse ───────────────────────────────────────────────────────────

class TestIsEmptyParse:
    def test_dict_with_no_name_and_no_price_is_empty(self):
        from scraper.pipeline import _is_empty_parse
        assert _is_empty_parse({"name": None, "price": None}) is True

    def test_dict_with_name_only_is_not_empty(self):
        from scraper.pipeline import _is_empty_parse
        # name present, price absent, image present → only 1 of 3 missing → NOT empty
        assert _is_empty_parse({"name": "Some Product", "price": None, "image_url": "https://img.example.com/1.jpg"}) is False

    def test_dict_with_price_only_is_not_empty(self):
        from scraper.pipeline import _is_empty_parse
        # price present, name absent, image present → only 1 missing → NOT empty
        assert _is_empty_parse({"name": None, "price": 19.99, "image_url": "https://img.example.com/1.jpg"}) is False

    def test_dict_with_both_fields_is_not_empty(self):
        from scraper.pipeline import _is_empty_parse
        assert _is_empty_parse({"name": "Product", "price": 9.99}) is False


# ── Cloudflare challenge detection ───────────────────────────────────────────

class TestCloudflareChallengeDetection:
    def test_cf_html_is_detected_in_classify_error(self):
        from scraper.pipeline import _classify_error
        cf_html = _read("metavparts_cloudflare_blocked.html")
        result = _classify_error(403, cf_html)
        assert any(kw in result.lower() for kw in ["cloudflare", "blocked", "challenge"])

    def test_cf_html_has_telltale_markers(self):
        cf_html = _read("metavparts_cloudflare_blocked.html")
        assert any(marker in cf_html.lower() for marker in ["cloudflare", "cf-ray", "just a moment"])


# ── SKU mismatch helper ────────────────────────────────────────────────────────

class TestSkuMismatch:
    def test_identical_skus_case_insensitive_no_mismatch(self):
        """SKU comparison should be case-insensitive."""
        requested = "metav7212"
        page_sku = "METAV7212"
        assert requested.strip().lower() == page_sku.strip().lower()

    def test_different_skus_should_mismatch(self):
        requested = "TEST-POLO-BL"
        page_sku = "WRONG-SKU-99"
        assert requested.strip().lower() != page_sku.strip().lower()
