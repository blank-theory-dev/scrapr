"""Shared pytest fixtures for scrapr test suite."""
from __future__ import annotations

from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def neto_valid_html() -> str:
    """A well-formed Neto product page for SKU TEST-POLO-BL."""
    return (FIXTURES_DIR / "neto_product_valid.html").read_text()


@pytest.fixture
def neto_wrong_sku_html() -> str:
    """A Neto product page whose page-SKU (WRONG-SKU-99) differs from the requested SKU."""
    return (FIXTURES_DIR / "neto_product_wrong_sku.html").read_text()


@pytest.fixture
def neto_404_html() -> str:
    """A Neto 404 / product-not-found page."""
    return (FIXTURES_DIR / "neto_404.html").read_text()


@pytest.fixture
def neto_blocked_html() -> str:
    """A Cloudflare challenge page returned instead of the product page."""
    return (FIXTURES_DIR / "neto_cloudflare_blocked.html").read_text()


@pytest.fixture
def neto_cfg():
    """The neto_default SiteConfig entry."""
    from scraper.config import SITE_CONFIGS
    return SITE_CONFIGS["neto_default"]
