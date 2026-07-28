"""End-to-end pipeline tests with mocked network responses."""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    p = FIXTURES / name
    if not p.exists():
        pytest.skip(f"Fixture not found: {name}")
    return p.read_text()


def _make_mock_client(responses: dict):
    """Build an AsyncSession mock where each URL maps to (status, html)."""

    class _MockSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            resp = MagicMock()
            url_str = str(url)
            # Match by substring
            matched = None
            for key, val in responses.items():
                if key in url_str:
                    matched = val
                    break
            if matched is None:
                matched = (404, "Not found", url_str)
            status, html, final_url = matched if len(matched) == 3 else (*matched, url_str)
            resp.status_code = status
            resp.text = html
            resp.url = final_url
            return resp

    return _MockSession()


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Happy path ────────────────────────────────────────────────────────────────

class TestNetoScrapeItemsHappyPath:
    def test_successful_neto_sku_returns_product_data(self):
        from scraper.pipeline import scrape_items

        html = _read_fixture("metavparts_METAV7212_raw.html")
        url = "https://www.metavparts.com.au/p/METAV7212"

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client({"/p/METAV7212": (200, html, url)})
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "METAV7212"}],
                    "Neto",
                    "https://www.metavparts.com.au",
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r["sku"] == "METAV7212"
        assert r["name"] is not None
        assert r["name"] != "Information"
        assert r["price"] is not None
        assert r["error"] is None

    def test_progress_callback_called_for_each_sku(self):
        from scraper.pipeline import scrape_items

        html = _read_fixture("metavparts_METAV7212_raw.html")
        url = "https://www.metavparts.com.au/p/METAV7212"

        progress_events = []

        def on_progress(completed, total, sku, state):
            progress_events.append((completed, total, sku, state))

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client({"/p/METAV7212": (200, html, url)})
            MockSession.return_value = mock_client

            run_async(
                scrape_items(
                    [{"sku": "METAV7212"}],
                    "Neto",
                    "https://www.metavparts.com.au",
                    None,
                    concurrency=1,
                    delay_ms=0,
                    on_progress=on_progress,
                )
            )

        assert len(progress_events) == 1
        completed, total, sku, state = progress_events[0]
        assert completed == 1
        assert total == 1
        assert sku == "METAV7212"
        assert state == "completed"

    def test_progress_fires_for_every_sku_in_batch(self):
        from scraper.pipeline import scrape_items

        html = _read_fixture("metavparts_METAV7212_raw.html")
        items = [{"sku": f"SKU{i}"} for i in range(5)]
        progress_events = []

        def on_progress(completed, total, sku, state):
            progress_events.append((completed, total))

        responses = {f"/p/SKU{i}": (200, html, f"https://www.metavparts.com.au/p/SKU{i}") for i in range(5)}

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client(responses)
            run_async(
                scrape_items(items, "Neto", "https://www.metavparts.com.au", None, 2, 0, on_progress=on_progress)
            )

        assert len(progress_events) == 5
        completed_counts = [c for c, _ in progress_events]
        assert sorted(completed_counts) == [1, 2, 3, 4, 5]


# ── Error path ────────────────────────────────────────────────────────────────

class TestNetoScrapeItemsErrors:
    def test_cloudflare_403_returns_typed_blocked_error(self):
        from scraper.pipeline import scrape_items

        cf_html = _read_fixture("metavparts_cloudflare_blocked.html")
        url = "https://www.metavparts.com.au/p/METAV575"

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client({"/p/METAV575": (403, cf_html, url)})
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "METAV575"}],
                    "Neto",
                    "https://www.metavparts.com.au",
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r["error"] is not None
        assert "blocked" in r["error"].lower() or "403" in r["error"]

    def test_404_returns_not_found_error(self):
        from scraper.pipeline import scrape_items

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client({"/p/NOTEXIST": (404, "Not found", "https://www.metavparts.com.au/p/NOTEXIST")})
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "NOTEXIST"}],
                    "Neto",
                    "https://www.metavparts.com.au",
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r["error"] is not None
        assert "not_found" in r["error"].lower() or "404" in r["error"]

    def test_no_origin_returns_construction_error(self):
        from scraper.pipeline import scrape_items

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client({})
            results = run_async(
                scrape_items(
                    [{"sku": "METAV575"}],
                    "Neto",
                    None,  # No origin
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        assert results[0]["error"] is not None

    def test_progress_callback_marks_failed_on_error(self):
        from scraper.pipeline import scrape_items

        cf_html = _read_fixture("metavparts_cloudflare_blocked.html")
        events = []

        def on_progress(completed, total, sku, state):
            events.append(state)

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client({"/p/METAV575": (403, cf_html, "https://www.metavparts.com.au/p/METAV575")})
            MockSession.return_value = mock_client
            run_async(
                scrape_items(
                    [{"sku": "METAV575"}],
                    "Neto",
                    "https://www.metavparts.com.au",
                    None,
                    concurrency=1,
                    delay_ms=0,
                    on_progress=on_progress,
                )
            )

        assert events == ["failed"]


# ── Circuit breaker ───────────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_dead_skus_do_not_open_the_circuit(self):
        """A run of 404s is normal data, not an outage.

        Auditing discontinued parts means long stretches of 404. Counting those
        as blocks made the tool abandon the run exactly when it was doing its
        job — 8 dead SKUs used to come back as 5 not_found + 3 circuit_open.
        """
        from scraper.pipeline import scrape_items

        origin = "https://store.example.com"
        skus = [{"sku": f"DEAD-{i:03d}"} for i in range(8)]

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client({})  # everything 404s

            results = run_async(
                scrape_items(skus, "Neto", origin, None, concurrency=1, delay_ms=0)
            )

        assert len(results) == 8
        assert all("not_found" in (r.get("error") or "") for r in results), \
            [r.get("error") for r in results]
        assert not any("circuit_open" in (r.get("error") or "") for r in results)

    def test_real_blocks_still_open_the_circuit(self):
        """403s are a refusal, and should still stop us hammering the site."""
        from scraper.pipeline import scrape_items

        origin = "https://store.example.com"
        skus = [{"sku": f"SKU-{i:03d}"} for i in range(8)]
        blocked = (403, "<html>Just a moment... cloudflare</html>")

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client({"/p/": blocked})

            results = run_async(
                scrape_items(skus, "Neto", origin, None, concurrency=1, delay_ms=0)
            )

        assert any("circuit_open" in (r.get("error") or "") for r in results), \
            "circuit breaker should trip on repeated 403s"


# ── Checkpointing ─────────────────────────────────────────────────────────────

class TestCheckpoint:
    def test_rows_survive_on_disk_and_second_run_resumes(self, tmp_path):
        """An interrupted run must keep finished rows; re-running skips them."""
        from scraper.pipeline import scrape_items

        origin = "https://store.example.com"
        ckpt = tmp_path / "run.csv"
        valid_html = _read_fixture("neto_product_valid.html")

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client(
                {"/p/TEST-POLO-BL": (200, valid_html)}
            )
            run_async(scrape_items(
                [{"sku": "TEST-POLO-BL"}], "Neto", origin, None,
                concurrency=1, delay_ms=0, checkpoint_path=str(ckpt),
            ))

        assert ckpt.exists(), "checkpoint file was never written"
        assert "TEST-POLO-BL" in ckpt.read_text()

        # Second run over the same SKU should skip it entirely (resume).
        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            MockSession.return_value = _make_mock_client({})
            again = run_async(scrape_items(
                [{"sku": "TEST-POLO-BL"}], "Neto", origin, None,
                concurrency=1, delay_ms=0, checkpoint_path=str(ckpt),
            ))
        assert again == [], "already-done SKU should have been skipped"


# ── Neto URL fallback ─────────────────────────────────────────────────────────

class TestNetoUrlFallback:
    def test_falls_back_to_second_pattern_on_404(self):
        """When /p/{sku} returns 404, the pipeline should try the next url_pattern.

        The shipped default is ["/p/{sku}"] alone — /buy/{sku} 404s on every store
        we tested, so carrying it cost an extra request per discontinued SKU. The
        fallback *mechanism* still matters for stores on other themes, so this
        configures a second pattern explicitly rather than relying on the default.
        """
        from scraper.pipeline import scrape_items
        from scraper.config import SITE_CONFIGS

        cfg = SITE_CONFIGS["neto_default"]
        valid_html = _read_fixture("neto_product_valid.html")
        origin = "https://store.example.com"

        with patch.object(cfg, "url_patterns", ["/p/{sku}", "/buy/{sku}"]), \
                patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client(
                {
                    "/p/TEST-POLO-BL": (404, "Not found", f"{origin}/p/TEST-POLO-BL"),
                    "/buy/TEST-POLO-BL": (200, valid_html, f"{origin}/buy/TEST-POLO-BL"),
                }
            )
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "TEST-POLO-BL"}],
                    "Neto",
                    origin,
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r.get("error") is None or "not_found" not in r.get("error", ""), \
            "Expected fallback to succeed but got: " + str(r.get("error"))


# ── SKU mismatch detection ────────────────────────────────────────────────────

class TestNetoSkuMismatch:
    def test_sku_mismatch_produces_error(self):
        """When the page's SKU differs from the requested SKU, an error is set."""
        from scraper.pipeline import scrape_items

        wrong_sku_html = _read_fixture("neto_product_wrong_sku.html")
        origin = "https://store.example.com"

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client(
                {"/p/TEST-POLO-BL": (200, wrong_sku_html, f"{origin}/p/TEST-POLO-BL")}
            )
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "TEST-POLO-BL"}],
                    "Neto",
                    origin,
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r.get("error") is not None
        assert "mismatch" in r["error"].lower() or "WRONG-SKU-99" in r["error"]


# ── Empty parse detection ─────────────────────────────────────────────────────

class TestNetoEmptyParseDetection:
    def test_empty_200_page_returns_parse_empty_error(self):
        """A 200 response with no product data triggers parse_empty error."""
        from scraper.pipeline import scrape_items

        empty_html = "<html><body><h1>Welcome to our store</h1></body></html>"
        origin = "https://store.example.com"

        with patch("scraper.pipeline.requests.AsyncSession") as MockSession:
            mock_client = _make_mock_client(
                {"/p/TEST-SKU": (200, empty_html, f"{origin}/p/TEST-SKU")}
            )
            MockSession.return_value = mock_client

            results = run_async(
                scrape_items(
                    [{"sku": "TEST-SKU"}],
                    "Neto",
                    origin,
                    None,
                    concurrency=1,
                    delay_ms=0,
                )
            )

        assert len(results) == 1
        r = results[0]
        assert r.get("error") is not None
        assert "parse_empty" in r["error"].lower() or "no product data" in r["error"].lower()
