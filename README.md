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
-   **Page Crawler**: Crawl category pages to discover and scrape products.
-   **Export**: Download results as CSV.

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

### Modes

1.  **SKUs**: Paste a list of SKUs and URLs to scrape specific items.
2.  **Page Crawler**: Enter a category page URL to scrape all products found on that page.
3.  **Catalog Indexer** (Shopify Only):
    -   Select "Catalog Indexer" from the sidebar.
    -   Enter the Shopify site URL (e.g., `https://example.com`).
    -   Click "Index Catalog".
    -   Download the full catalog as CSV or a list of SKUs.

## Deployment

This app is ready for **Streamlit Community Cloud**.

1.  Push this code to a GitHub repository.
2.  Go to [share.streamlit.io](https://share.streamlit.io).
3.  Deploy the app by selecting your repository and `app.py`.

*Note: `nest_asyncio` is included to ensure stability in cloud environments.*
