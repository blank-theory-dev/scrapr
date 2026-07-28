# Universal SKU Scraper

A powerful, multi-CMS product scraper and catalog indexer built with Streamlit and Python.

## Features

-   **Multi-CMS Support**: Specialized scraping for Shopify, WordPress (WooCommerce), and Neto.
-   **Shopify Catalog Indexer**: Instantly fetch and index the entire product catalog from any Shopify site (using `products.json`).
-   **Smart Extraction**: Automatically extracts:
    -   SKU
    -   Group ID (Product ID) & Variant ID
    -   Name, Price, RRP, Discount %
    -   Images & Breadcrumbs
    -   Category (with fallback)
-   **Config-free fallback**: When a site's CSS selectors come up empty, the parser reads
    the page's own structured data instead — JSON-LD, embedded shop config, microdata,
    then OpenGraph. New stores work without hand-tuning selectors.
-   **Resumable runs**: Rows are written to disk as they're scraped, so an interrupted run
    keeps its work and re-running picks up where it stopped.
-   **Export**: Download results as CSV.

> **Run this locally.** These storefronts sit behind Cloudflare, which challenges
> datacenter IP addresses — the same code that returns 0/16 on Streamlit Community Cloud
> returns 16/16 from an ordinary connection. See `RUNBOOK.md` §5.

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/yourusername/sku-scraper.git
    cd sku-scraper
    ```

2.  **Create a virtual environment** (recommended):
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows: .venv\Scripts\activate
    ```

3.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

Run the Streamlit app:

```bash
streamlit run app.py
```

Set the **Base URL** to the store, pick the CMS, then paste SKUs or upload a CSV with a
`sku` (or `url`) column. Results appear in a table with a CSV download.

Runs resume by default — if one is interrupted, running it again scrapes only what's
missing. Tick **Re-fetch everything** to ignore the saved rows and pull fresh prices.

## Deployment

**Run it on a normal connection, not a cloud host.** Cloudflare fronts these storefronts
and scores datacenter IP addresses as bots before the page is even built, so a cloud
deployment gets `403` on every request while the identical code succeeds from a laptop.
Measured 2026-07-28: the same 16 SKUs returned 0/16 on Streamlit Community Cloud and
16/16 locally, with no code change.

This is not fixable with a different HTTP client or a stealthier browser — headless Chrome
is blocked where `curl_cffi` (no JavaScript engine at all) succeeds. `RUNBOOK.md` §5 has
the full measurements and the dead ends, so nobody re-litigates them.

If a hosted URL is genuinely required, route the fetch layer through a residential proxy:
add `proxies={"https": PROXY_URL}` to the `AsyncSession` in `scraper/pipeline.py`. At this
volume a ~$5 non-expiring top-up lasts years — far cheaper than the $49–149/month scraping
APIs, which are selling the same thing.

*Note: `nest_asyncio` is included to keep Streamlit's event loop happy.*
