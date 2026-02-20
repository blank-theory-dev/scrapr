
import asyncio
from unittest.mock import MagicMock, AsyncMock
from scraper.pipeline import scrape_items

# Mock search HTML with a product link
SEARCH_HTML = """
<html>
<body>
    <div class="product-item">
        <a href="/products/test-product">Test Product</a>
    </div>
</body>
</html>
"""

# Mock product HTML
PRODUCT_HTML = """
<html>
<head>
    <title>Test Product</title>
    <meta property="og:price:amount" content="50.00" />
</head>
<body>
    <h1>Test Product</h1>
</body>
</html>
"""

async def test_search_resolution():
    # Mock httpx client behavior
    # We can't easily mock the internal client context manager in scrape_items without refactoring,
    # so we'll rely on the fact that _fetch calls client.get.
    # However, scrape_items creates its own client. 
    # To test this without hitting the network, we'd need to patch httpx.AsyncClient.
    
    # For this quick verification, we will just verify the logic by inspecting the code change 
    # or we can try to patch it. Let's try patching.
    
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
            m.url = url
            if "search?" in str(url):
                m.text = SEARCH_HTML
                m.url = "https://example.com/search?q=123"
            else:
                m.text = PRODUCT_HTML
                m.url = "https://example.com/products/test-product"
            return m

    httpx.AsyncClient = MockClient
    
    try:
        items = [{"sku": "123"}]
        results = await scrape_items(
            items=items,
            cms_choice="Shopify",
            origin="https://example.com",
            url_pattern=None, # Trigger search fallback
            concurrency=1,
            delay_ms=0
        )
        
        print("Results:", results)
        
        if results and results[0].get("product_url") == "https://example.com/products/test-product":
            print("SUCCESS: Resolved search URL to product URL.")
        else:
            print("FAILURE: Did not resolve URL correctly.")
            
    finally:
        httpx.AsyncClient = original_client

if __name__ == "__main__":
    asyncio.run(test_search_resolution())
