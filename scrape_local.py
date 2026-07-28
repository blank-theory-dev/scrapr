#!/usr/bin/env python3
"""Local browser-driven scraper. LIKELY OBSOLETE — see the note below.

    python3 scrape_local.py "Machter New Eliminated Parts July 2026.csv"
    python3 scrape_local.py input.csv -o out.csv --cms Neto

This file's original premise was wrong, and it is kept only as a fallback for a
site that genuinely defeats the normal path.

The premise was: "curl_cffi cannot pass Cloudflare's managed challenge, only a
headed browser can." Measured 2026-07-28, both halves are false:

  - curl_cffi (impersonate="chrome") gets a clean HTTP 200 from machter.com.au
    and metavparts.com.au on a first, cold request from a residential IP. No
    challenge is issued at all.
  - Headless Chrome is *blocked* where curl_cffi succeeds — tested across four
    configurations including patchright. A JS engine is not what Cloudflare is
    asking for; it scores the connection before the page renders.
  - cf_clearance is never minted on a residential IP, so nothing is cached
    between pages. Session reuse of __cf_bm is what actually made this fast.

What was really breaking the hosted app was its datacenter IP, not its HTTP
client. `streamlit run app.py` on a normal connection does everything this does,
with progress, checkpointing and resume — and without the Playwright dependency.

Known defect if you do use it: page.goto()'s response is discarded, so HTTP
status is never checked and 404 pages get parsed as if they were products.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper.parser import parse_product
from scraper.config import SITE_CONFIGS

CMS_MAP = {"Neto": "neto_default", "Shopify": "shopify_default",
           "WordPress (WooCommerce)": "wordpress_default", "WooCommerce": "wordpress_default"}

COLUMNS = ["sku", "error", "product_url", "name", "price", "sale_price", "rrp",
           "discount_percent", "category", "breadcrumbs", "image_url",
           "image_url_2", "image_url_3", "image_url_4", "image_url_5", "url"]


def read_rows(path: Path) -> list[dict]:
    """Pull (sku, url) pairs from any CSV. Tolerates the client's header-in-row-4
    layout by scanning every row for a cell that looks like a product URL."""
    rows: list[dict] = []
    seen: set = set()
    with path.open(newline="", encoding="utf-8-sig") as f:
        for cells in csv.reader(f):
            url = next((c.strip() for c in cells if c.strip().lower().startswith("http")), None)
            if not url or url.lower() in ("url",):
                continue
            # SKU = first non-url, non-empty cell on the row (the client's "Field Name" col)
            sku = next((c.strip() for c in cells if c.strip() and not c.strip().lower().startswith("http")), None)
            key = (sku or "", url)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"sku": sku, "url": url})
    return rows


def wait_past_challenge(page, timeout_ms: int = 30000) -> None:
    """Block until the Cloudflare interstitial clears (or timeout)."""
    try:
        page.wait_for_function(
            "() => !document.title.toLowerCase().includes('just a moment')",
            timeout=timeout_ms,
        )
    except Exception:
        pass  # fall through; parser + empty-guard will flag a still-blocked page


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("results.csv"))
    ap.add_argument("--cms", default="Neto", choices=list(CMS_MAP))
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between pages")
    args = ap.parse_args()

    cfg = SITE_CONFIGS[CMS_MAP[args.cms]]
    rows = read_rows(args.input_csv)
    if not rows:
        print("No product URLs found in the CSV.", file=sys.stderr)
        return 1
    print(f"Found {len(rows)} items. Launching Chrome — a window will open; "
          "leave it be while it solves the Cloudflare check.")

    results: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        ctx = browser.new_context(locale="en-AU")
        page = ctx.new_page()
        for i, row in enumerate(rows, 1):
            url = row["url"]
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                wait_past_challenge(page)
                html = page.content()
                data = parse_product(html, page.url, cfg, row["sku"])
                if "just a moment" in html.lower():
                    data["error"] = "blocked:cloudflare — challenge did not clear"
                elif (data.get("name") or "").strip().lower() == "page not found":
                    # Neto serves a soft-404 (HTTP 200 "Page Not Found") for removed
                    # products — the client's URL is stale. Blank the junk it scraped.
                    data.update(name=None, price=None, image_url=None,
                                error="not_found: product removed from site (soft-404)")
                elif not data.get("name") and not data.get("price"):
                    data["error"] = "parse_empty: page loaded but no product data extracted"
                data.setdefault("sku", row["sku"])
                data["url"] = url
                results.append(data)
                state = "ERR " + data["error"] if data.get("error") else f"OK  {data.get('name') or ''}"[:70]
            except Exception as e:
                results.append({"sku": row["sku"], "url": url, "product_url": url,
                                "error": f"request_failed: {type(e).__name__}: {e}"})
                state = f"ERR request_failed: {e}"
            print(f"[{i}/{len(rows)}] {row['sku']}: {state}")
            time.sleep(args.delay)
        browser.close()

    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)

    errs = sum(1 for r in results if r.get("error"))
    print(f"\nWrote {len(results)} rows → {args.output}  ({errs} errors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
