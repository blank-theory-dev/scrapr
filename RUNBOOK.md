# Scrapr Operations Runbook

Quick reference for diagnosing and fixing scraping issues.

---

## 1. Quick Start

```bash
# Install
pip install -r requirements.txt

# Run the app
streamlit run app.py

# Run tests
pytest tests/ -v
```

## 2. Error Reference

Every failed row in the output CSV now has an **error** column with a typed prefix.

| Error prefix | Meaning | What to do |
|---|---|---|
| `blocked:cloudflare` | Cloudflare JS challenge — site uses bot protection | Add a delay, use a different IP/proxy, or retry later |
| `blocked:forbidden` | Plain HTTP 403 | Same as above; site has IP-based blocking |
| `blocked:rate_limited` | HTTP 429 too many requests | Reduce concurrency, increase delay, wait and retry |
| `not_found` | HTTP 404 | SKU or URL is wrong; verify the SKU list |
| `server_error:503` | Site is down or overloaded | Retry later |
| `parse_empty` | Page loaded (200) but no product data extracted | Site may render products via JavaScript; try a different CMS config, or check the selector against the page HTML |
| `parse_mismatch` | Page loaded but the SKU on the page ≠ requested SKU | The URL pattern may be wrong, or the SKU redirected to a different product |
| `circuit_open` | 5+ consecutive requests to this domain were blocked | Domain has IP-blocked the session; wait, restart, or use a proxy |
| `request_failed` | Network-level error (DNS, timeout, etc.) | Check internet connection; site may be down |
| `parse_timeout` | Parser took >30 s (very large page) | Report the URL; may need a whitelist exclusion |
| `parse_error` | Unexpected exception in parser | Report as a bug — include the URL |

---

## 3. Diagnosing a Slow or Hanging Run

1. **Check the progress bar** — it now shows `completed / total` + ETA.  
   If progress stalls for more than 2–3 minutes with no movement, the session is likely blocked by Cloudflare.

2. **Check the error breakdown panel** (shown after completion).  
   A majority of `blocked:cloudflare` errors means the storefront uses Cloudflare JS challenges.

3. **Console logs** — if running from the terminal you will see `Indexing Shopify Catalog…` for Shopify, or a request error traceback for hard failures.

---

## 4. Neto Stores — Known Behaviours

| Symptom | Cause | Fix |
|---|---|---|
| All SKUs return `blocked:cloudflare` | Cloudflare JS challenge is active | Retry after a few hours from a different IP; consider using a residential proxy |
| SKU returns `parse_mismatch` | `/p/{sku}` URL returns a different product page | The store may use a different URL format; try setting a custom URL pattern |
| SKU returns `parse_empty` | Page loads but has no price/name | The store's theme may use JavaScript rendering; selectors may need updating |
| `discount_percent` is None | Item is full price (no RRP on page) | Normal — not a bug |

### URL Patterns for Neto

The scraper tries these patterns in order on 404:
1. `/p/{sku}` (primary)
2. `/buy/{sku}` (fallback)

If the store uses a different format (e.g. `/product/{sku}`), set it in `SITE_CONFIGS["neto_default"].url_patterns`.

---

## 5. Cloudflare Bypass Notes

This scraper uses `curl_cffi` with Chrome impersonation (`impersonate="chrome"`).  
It also visits the homepage first to warm the session (set Cloudflare cookies).

**This is NOT a guaranteed bypass.** Cloudflare's latest JS challenges (`Just a moment…`) 
require JavaScript execution and cannot be solved without a real browser. 

When the site uses this level of protection, your options are:
- **Manual copy**: open each product page in a browser, copy the SKU data
- **Browser automation**: Playwright/Selenium (significant setup)  
- **Residential proxy services**: Route requests through ISP IPs (paid service)

---

## 6. Running Tests Locally

```bash
# Run all tests (offline — uses fixtures, no network)
pytest tests/ -v

# Run a specific test file
pytest tests/test_neto_parser.py -v

# Run only the parser tests against the real fixture
pytest tests/test_neto_parser.py::TestNetoParserRealFixture -v
```

The fixture at `tests/fixtures/metavparts_METAV7212_raw.html` is a real HTML snapshot 
from metavparts.com.au captured at development time. Tests against it are deterministic 
and do not require network access.

---

## 7. Adding a New Store or Selector Override

1. Open `scraper/config.py`
2. Add a new entry to `SITE_CONFIGS` (or copy `neto_default` and adjust selectors)
3. Add the store's origin to the `CMS_MAP` in `scraper/pipeline.py` if it needs a new key
4. Add at least one fixture HTML file to `tests/fixtures/` 
5. Write a test in `tests/test_neto_parser.py` (or a new file) to verify extraction

---

## 8. Escalation Contacts

| Issue | Contact |
|---|---|
| Cloudflare blocking (all SKUs) | Engineering — consider proxy/browser solution |
| Missing or wrong selectors for a specific store | Engineering — update `scraper/config.py` |
| CSV columns look wrong | Check the column mapping in `app.py → _render_results` |
