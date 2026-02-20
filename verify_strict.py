import asyncio
from scraper.pipeline import scrape_items
import json

async def main():
    # SKUs that were previously returning "Ghost Glove" incorrectly
    items = [
        {"sku": "72787"},  # Should fail or match exact
        {"sku": "115400"}, # Should match "Ghost Glove" (XS) - from debug_product.html
        {"sku": "999999"}, # Should fail
    ]
    
    print("Running strict verification test...")
    results = await scrape_items(
        items=items,
        cms_choice="Shopify",
        origin="https://legear.com.au",
        url_pattern=None,
        concurrency=1,
        delay_ms=500
    )
    
    for r in results:
        print(f"SKU: {r.get('sku')}")
        print(f"Name: {r.get('name')}")
        print(f"Error: {r.get('error')}")
        print("-" * 20)

if __name__ == "__main__":
    asyncio.run(main())
