
import asyncio
from unittest.mock import MagicMock
from scraper.pipeline import scrape_items
from scraper.config import SITE_CONFIGS

# Mock search HTML with 2 product links
SEARCH_HTML = """
<html>
<body>
    <div class="product-item">
        <a href="/products/wrong-product">Wrong Product</a>
    </div>
    <div class="product-item">
        <a href="/products/correct-product">Correct Product</a>
    </div>
</body>
</html>
"""

# Mock wrong product HTML
WRONG_PRODUCT_HTML = """
<html>
<head>
    <title>Wrong Product</title>
    <script>
        var meta = {"product":{"id":111,"variants":[{"sku":"WRONG-SKU"}]}};
    </script>
</head>
<body>
    <h1>Wrong Product</h1>
</body>
</html>
"""

# Mock correct product HTML
CORRECT_PRODUCT_HTML = """
<html>
<head>
    <title>Correct Product</title>
    <script>
        var meta = {"product":{"id":222,"variants":[{"sku":"CORRECT-SKU"}]}};
    </script>
</head>
<body>
    <h1>Correct Product</h1>
</body>
</html>
"""

async def test_search_verification():
    import httpx
    
    original_client = httpx.AsyncClient
    
    class MockClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        
        async def get(self, url, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.url = str(url)
            
            if "search?" in str(url):
                m.text = SEARCH_HTML
                m.url = "https://example.com/search?q=CORRECT-SKU"
            elif "wrong-product" in str(url):
                m.text = WRONG_PRODUCT_HTML
                m.url = "https://example.com/products/wrong-product"
            elif "correct-product" in str(url):
                m.text = CORRECT_PRODUCT_HTML
                m.url = "https://example.com/products/correct-product"
            return m

    httpx.AsyncClient = MockClient
    
    try:
        # We search for CORRECT-SKU
        items = [{"sku": "CORRECT-SKU"}]
        results = await scrape_items(
            items=items,
            cms_choice="Shopify",
            origin="https://example.com",
            url_pattern=None, # Trigger search fallback
            concurrency=1,
            delay_ms=0
        )
        
        print("Results:", results)
        
        if results and results[0].get("sku") == "CORRECT-SKU":
            print("SUCCESS: Found correct product by verifying SKU.")
        else:
            print(f"FAILURE: Did not find correct product. Got: {results[0].get('sku') if results else 'None'}")
            
    finally:
        httpx.AsyncClient = original_client

if __name__ == "__main__":
    asyncio.run(test_search_verification())
