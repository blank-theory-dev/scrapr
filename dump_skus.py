import asyncio
import logging
from scraper.shopify_catalog import ShopifyCatalogIndexer

# Configure logging
logging.basicConfig(level=logging.INFO)

async def main():
    indexer = ShopifyCatalogIndexer("https://legear.com.au")
    print("Indexing catalog...")
    await indexer.fetch_catalog()
    print(f"Indexed {len(indexer.catalog)} variants.")
    
    with open("all_skus.txt", "w") as f:
        for sku in sorted(indexer.catalog.keys()):
            f.write(f"{sku}\n")
    print("Saved all SKUs to all_skus.txt")

if __name__ == "__main__":
    asyncio.run(main())
