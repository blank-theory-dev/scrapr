import asyncio
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import streamlit as st
import nest_asyncio

# Fix for Streamlit's asyncio loop
nest_asyncio.apply()

from scraper.pipeline import scrape_items, scrape_by_page
from scraper.config import SITE_CONFIGS

def _run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

def _normalise_rows(rows: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
    seen = set()
    out = []
    for r in rows:
        # Case-insensitive key lookup
        sku = (r.get("SKU") or r.get("sku") or "").strip() or None
        url = (r.get("URL") or r.get("url") or "").strip() or None
        
        if not sku and not url:
            continue
            
        key = (sku or "").lower(), (url or "").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"sku": sku, "url": url})
    return out

def main():
    st.set_page_config(page_title="B_T SKU Scrapr", layout="wide")

    # Custom CSS for "Premium" look
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');

    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
    }

    /* Titles */
    h1, h2, h3 {
        color: var(--text-color);
    }
    h1 {
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 0.5rem;
    }
    h2, h3 {
        font-weight: 600;
        opacity: 0.9;
    }

    /* Buttons */
    .stButton>button {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: white;
        border-radius: 8px;
        border: none;
        padding: 0.6rem 1.2rem;
        font-weight: 600;
        box-shadow: 0 4px 6px -1px rgba(79, 70, 229, 0.2), 0 2px 4px -1px rgba(79, 70, 229, 0.1);
        transition: all 0.2s ease;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
        box-shadow: 0 10px 15px -3px rgba(79, 70, 229, 0.3), 0 4px 6px -2px rgba(79, 70, 229, 0.15);
        transform: translateY(-1px);
    }
    .stButton>button:active {
        transform: translateY(0);
    }

    /* Inputs - Use Secondary Background for contrast if available, else standard */
    .stTextInput>div>div>input, .stTextArea>div>div>textarea {
        background-color: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 8px;
    }
    .stTextInput>div>div>input:focus, .stTextArea>div>div>textarea:focus {
        border-color: #6366f1;
        box-shadow: 0 0 0 1px #6366f1;
    }

    /* Sidebar - Streamlit handles background, we just refine borders */
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(128, 128, 128, 0.1);
    }
    
    /* Layout containers */
    .block-container {
        padding-top: 2rem;
        max-width: 1200px;
    }
    
    /* Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 2rem;
    }
    .stTabs [data-baseweb="tab"] {
        height: auto;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 4px;
        gap: 0;
        padding-top: 10px;
        padding-bottom: 10px;
        color: var(--text-color);
        opacity: 0.7;
    }
    .stTabs [aria-selected="true"] {
        color: #6366f1;
        opacity: 1;
        border-bottom-color: #6366f1;
    }

    </style>
    """, unsafe_allow_html=True)

    st.title("B_T SKU Scrapr")
    st.markdown("### Extract product data from Neto, Shopify, and WooCommerce")

    # Sidebar for configuration
    with st.sidebar:
        st.image("assets/logo.png", use_column_width=True)


        mode = st.radio("Mode", ["SKUs", "Page Crawler"])
        
        cms_choice = st.selectbox(
            "CMS / Site Type",
            ["Neto", "Shopify", "WordPress (WooCommerce)"],
            index=1  # Default to Shopify
        )
        
        # Hardcoded defaults since UI controls are removed
        concurrency = 5
        delay_ms = 0
        
        fast_mode = False
        if cms_choice == "Shopify":
            st.markdown("---")
            fast_mode = st.checkbox("Fast Mode (Catalog Only)", 
                                  help="Skip page visits to avoid 429 errors. No breadcrumbs, but instant results.")
        
        st.markdown("---")
        if st.button("Clear Cache", help="Force re-download of catalog data"):
            st.cache_resource.clear()
            st.success("Cache cleared!")

    if mode == "SKUs":
        origin = st.text_input("Base URL (Origin)", "https://legear.com.au")
        # url_pattern removed as per request
        url_pattern = "" 


        # Input Tabs
        tab1, tab2 = st.tabs(["Manual Input", "CSV Upload"])
        
        sku_input = ""
        url_input = ""
        csv_file = None

        with tab1:
            # Removed Enter URLs section, so just show SKU input
            sku_input = st.text_area("Enter SKUs (one per line)", height=150, placeholder="ABC-123\nXYZ-789")
        
        with tab2:
            csv_file = st.file_uploader("Upload CSV (must have 'sku' or 'url' column)", type=["csv"])

        if st.button("Scrape Items", use_container_width=True):
            # Gather inputs
            raw_rows = []
            
            # 1. CSV
            if csv_file:
                try:
                    df_in = pd.read_csv(csv_file, dtype=str, keep_default_na=False)
                    raw_rows.extend(df_in.to_dict(orient="records"))
                except Exception as e:
                    st.error(f"Failed reading CSV: {e}")
                    return

            # 2. Manual SKUs
            if sku_input.strip():
                raw_rows.extend([{"sku": s.strip()} for s in sku_input.splitlines() if s.strip()])

            # 3. Manual URLs (Removed from UI)
            # if url_input.strip():
            #    raw_rows.extend([{"url": u.strip()} for u in url_input.splitlines() if u.strip()])

            items = _normalise_rows(raw_rows)

            if not items:
                st.warning("Please provide at least one SKU or URL.")
                return

            # 4. Prepare Indexer (Cached)
            indexer = None
            if cms_choice == "Shopify" and origin:
                from scraper.shopify_catalog import ShopifyCatalogIndexer
                
                @st.cache_resource(ttl=3600, show_spinner="Indexing Shopify Catalog...")
                def get_cached_indexer(url: str):
                    idx = ShopifyCatalogIndexer(url)
                    # We need to run async fetch in a sync wrapper for st.cache_resource?
                    # Or we can cache the object and run fetch if not indexed?
                    # Better: Run the fetch here using _run
                    _run(idx.fetch_catalog())
                    return idx
                
                try:
                    indexer = get_cached_indexer(origin)
                    if not indexer.catalog:
                        st.warning("Catalog download blocked (429). Switching to slow search mode.")
                        indexer = None # Force fallback to legacy search
                    else:
                        st.success(f"Using cached catalog ({len(indexer.catalog)} variants)")
                except Exception as e:
                    st.error(f"Failed to index catalog: {e}")
                    indexer = None

            with st.spinner(f"Scraping {len(items)} items..."):
                results = _run(scrape_items(
                    items, cms_choice, origin, url_pattern, concurrency, delay_ms, indexer=indexer, fast_mode=fast_mode
                ))
            
                st.session_state['sku_results'] = results
            
            st.success(f"Completed! Processed {len(results)} items.")
            
        # Display results from session state if available
        if 'sku_results' in st.session_state and st.session_state['sku_results']:
            results = st.session_state['sku_results']
            df = pd.DataFrame(results)
            
            # Ensure secondary image columns exist
            for i in range(2, 6):
                col = f"image_url_{i}"
                if col not in df.columns:
                    df[col] = None

            # Ensure all_variant_ids is string to avoid Arrow errors
            if "all_variant_ids" in df.columns:
                df["all_variant_ids"] = df["all_variant_ids"].astype(str)

            # Reorder columns if possible
            preferred = ["sku", "product_url", "name", "price", "rrp", "discount_percent", 
                       "group_id", "variant_id", "all_variant_ids",
                       "category", "breadcrumbs", "image_url", 
                       "image_url_2", "image_url_3", "image_url_4", "image_url_5",
                       "error", "url"]
            cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
            df = df[cols]

            # Column selection
            st.write("### Export Options")
            selected_cols = st.multiselect(
                "Choose columns to export:",
                options=list(df.columns),
                default=list(df.columns),
                key="sku_col_select"
            )

            if selected_cols:
                # Enforce original column order
                all_cols = list(df.columns)
                sorted_cols = sorted(selected_cols, key=all_cols.index)
                
                df_display = df[sorted_cols]
                st.dataframe(df_display, use_container_width=True)
                
                # CSV Download
                csv = df_display.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "Download CSV",
                    csv,
                    "results.csv",
                    "text/csv",
                    key='download-csv'
                )
            else:
                st.warning("Please select at least one column to export.")
        elif 'sku_results' in st.session_state and not st.session_state['sku_results']:
             st.info("No results found.")

    else:
        col1, col2 = st.columns(2)
        with col1:
            page_url = st.text_input("Category Page URL")
        with col2:
            max_items = st.number_input("Max Items", 1, 1000, 50)

        if st.button("Crawl Page", use_container_width=True):
            if not page_url:
                st.warning("Please enter a URL.")
                return

            with st.spinner("Crawling page..."):
                results = _run(scrape_by_page(
                    page_url, cms_choice, max_items, concurrency, delay_ms
                ))

                st.session_state['crawl_results'] = results

            st.success(f"Crawled {len(results)} items.")
            
        if 'crawl_results' in st.session_state and st.session_state['crawl_results']:
            results = st.session_state['crawl_results']
            df = pd.DataFrame(results)
            
            # Ensure secondary image columns exist
            for i in range(2, 6):
                col = f"image_url_{i}"
                if col not in df.columns:
                    df[col] = None

            # Column selection
            st.write("### Export Options")
            selected_cols = st.multiselect(
                "Choose columns to export:",
                options=list(df.columns),
                default=list(df.columns),
                key="crawler_col_select"
            )

            if selected_cols:
                # Enforce original column order
                all_cols = list(df.columns)
                sorted_cols = sorted(selected_cols, key=all_cols.index)
                
                df_display = df[sorted_cols]
                st.dataframe(df_display, use_container_width=True)
                
                csv = df_display.to_csv(index=False).encode('utf-8')
                st.download_button(
                    "Download CSV",
                    csv,
                    "crawl_results.csv",
                    "text/csv"
                )
            else:
                st.warning("Please select at least one column to export.")



if __name__ == "__main__":
    main()
