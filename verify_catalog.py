import asyncio
import logging
from scraper.pipeline import scrape_items

# Configure logging
logging.basicConfig(level=logging.INFO)

async def main():
    # Test SKUs
    # 72787: Should be missing (Strict match failed)
    # 117461: Should be found
    # 73302: Should be found (normalized from 073302)
    items = [
        {"sku": "72787", "url": None},
        {"sku": "117461", "url": None},
        {"sku": "73302", "url": None}
    ]
    
    # Simulate Caching: Initialize Indexer OUTSIDE scrape_items
    from scraper.shopify_catalog import ShopifyCatalogIndexer
    origin = "https://legear.com.au"
    
    print("Initializing Indexer (Simulating Cache)...")
    indexer = ShopifyCatalogIndexer(origin)
    await indexer.fetch_catalog()
    print(f"Indexer ready with {len(indexer.catalog)} items.")
    
    print("Running Scrape with Cached Indexer...")
    results = await scrape_items(
        items=items,
        cms_choice="Shopify",
        origin=origin,
        url_pattern=None,
        concurrency=1,
        delay_ms=100,
        indexer=indexer # Pass the pre-loaded indexer
    )
    
    print("\nResults:")
    for r in results:
        print(f"SKU: {r.get('sku')}")
        print(f"Name: {r.get('name')}")
        print(f"Price: {r.get('price')}")
        print(f"Error: {r.get('error')}")
        print("-" * 20)

if __name__ == "__main__":
    asyncio.run(main())
