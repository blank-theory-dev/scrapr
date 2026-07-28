"""B_T SKU Scrapr — Streamlit UI."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
import nest_asyncio

nest_asyncio.apply()

from scraper.pipeline import scrape_items
from scraper.config import SITE_CONFIGS


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _load_checkpoint(path: str) -> List[Dict]:
    """Rows saved by an earlier run of this origin, if any."""
    if not os.path.exists(path):
        return []
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except Exception:
        return []
    rows = df.to_dict(orient="records")
    for r in rows:
        for k in ("price", "sale_price", "rrp", "discount_percent"):
            v = r.get(k)
            r[k] = float(v) if v not in (None, "") else None
        for k, v in list(r.items()):
            if v == "":
                r[k] = None
    return rows


def _checkpoint_for(origin: str) -> str:
    """Per-origin checkpoint file, kept next to the app so a killed run is recoverable."""
    slug = "".join(c if c.isalnum() else "_" for c in (origin or "run"))[:60]
    d = Path(".scrapr_runs")
    d.mkdir(exist_ok=True)
    return str(d / f"{slug}.csv")


def _normalise_rows(rows: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
    seen: set = set()
    out: list = []
    for r in rows:
        sku = (r.get("SKU") or r.get("sku") or "").strip() or None
        url = (r.get("URL") or r.get("url") or "").strip() or None
        if not sku and not url:
            continue
        key = ((sku or "").lower(), (url or "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({"sku": sku, "url": url})
    return out


def _render_results(results: list, download_filename: str = "results.csv") -> None:
    """Render the results dataframe + download button."""
    df = pd.DataFrame(results)

    for i in range(2, 6):
        col = f"image_url_{i}"
        if col not in df.columns:
            df[col] = None

    df = df.drop(columns=[c for c in ["all_skus"] if c in df.columns], errors="ignore")

    preferred = [
        "sku", "error", "product_url", "name", "price", "sale_price", "rrp",
        "discount_percent", "category", "breadcrumbs", "image_url",
        "image_url_2", "image_url_3", "image_url_4", "image_url_5", "url",
    ]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df = df[cols]

    st.write("### Export Options")
    selected_cols = st.multiselect(
        "Choose columns to export:",
        options=list(df.columns),
        default=list(df.columns),
        key=f"col_select_{download_filename}",
    )

    if selected_cols:
        all_cols = list(df.columns)
        sorted_cols = sorted(selected_cols, key=all_cols.index)
        df_display = df[sorted_cols]
        st.dataframe(df_display, use_container_width=True)
        st.download_button(
            "Download CSV",
            df_display.to_csv(index=False).encode("utf-8"),
            download_filename,
            "text/csv",
        )
    else:
        st.warning("Please select at least one column to export.")


def main():
    st.set_page_config(page_title="B_T SKU Scrapr", layout="wide")

    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    h1 { font-weight: 700; letter-spacing: -0.02em; margin-bottom: 0.5rem; }
    h2, h3 { font-weight: 600; opacity: 0.9; }
    .stButton>button {
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: white; border-radius: 8px; border: none;
        padding: 0.6rem 1.2rem; font-weight: 600;
        box-shadow: 0 4px 6px -1px rgba(79,70,229,.2), 0 2px 4px -1px rgba(79,70,229,.1);
        transition: all .2s ease;
    }
    .stButton>button:hover {
        background: linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
        box-shadow: 0 10px 15px -3px rgba(79,70,229,.3), 0 4px 6px -2px rgba(79,70,229,.15);
        transform: translateY(-1px);
    }
    .stButton>button:active { transform: translateY(0); }
    .stTextInput>div>div>input, .stTextArea>div>div>textarea {
        background-color: var(--secondary-background-color);
        color: var(--text-color);
        border: 1px solid rgba(128,128,128,.2); border-radius: 8px;
    }
    .stTextInput>div>div>input:focus, .stTextArea>div>div>textarea:focus {
        border-color: #6366f1; box-shadow: 0 0 0 1px #6366f1;
    }
    [data-testid="stSidebar"] { border-right: 1px solid rgba(128,128,128,.1); }
    .block-container { padding-top: 2rem; max-width: 1200px; }
    .stTabs [data-baseweb="tab-list"] { gap: 2rem; }
    .stTabs [data-baseweb="tab"] {
        height: auto; white-space: pre-wrap; background-color: transparent;
        border-radius: 4px; padding: 10px; color: var(--text-color); opacity: .7;
    }
    .stTabs [aria-selected="true"] { color: #6366f1; opacity: 1; border-bottom-color: #6366f1; }
    </style>
    """, unsafe_allow_html=True)

    st.title("B_T SKU Scrapr")
    st.markdown("`v1.2.0`")
    st.markdown("### Extract product data from Neto, Shopify, and WooCommerce")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        logo = Path("assets/logo.png")
        if logo.exists():
            st.image(str(logo), use_container_width=True)

        cms_choice = st.selectbox(
            "CMS / Site Type",
            ["Neto", "Shopify", "WordPress (WooCommerce)"],
            index=0,
        )

        # These stores' robots.txt asks for Crawl-delay: 1. Two workers sleeping
        # 400ms each was ~5 req/s — impolite, and an independent reason to get
        # blocked. At a few hundred SKUs, sequential costs minutes and buys
        # reliability.
        concurrency = 1
        delay_ms = 1000

        fast_mode = False
        if cms_choice == "Shopify":
            st.markdown("---")
            fast_mode = st.checkbox(
                "Fast Mode (Catalog Only)",
                help="Skip page visits to avoid 429 errors. No breadcrumbs, but instant results.",
            )

        st.markdown("---")
        if st.button("Clear Cache", help="Force re-download of catalog data"):
            st.cache_resource.clear()
            st.success("Cache cleared!")

    origin = st.text_input("Base URL (Origin)", "https://legear.com.au").strip()
    url_pattern = ""

    tab1, tab2 = st.tabs(["Manual Input", "CSV Upload"])
    sku_input = ""
    csv_file = None

    with tab1:
        sku_input = st.text_area(
            "Enter SKUs (one per line)", height=150,
            placeholder="ABC-123\nXYZ-789",
        )

    with tab2:
        csv_file = st.file_uploader(
            "Upload CSV (must have 'sku' or 'url' column)", type=["csv"]
        )

    fresh_run = st.checkbox(
        "Re-fetch everything",
        help="By default an interrupted run resumes where it stopped. "
             "Tick this to ignore the saved rows and pull fresh prices.",
    )

    if st.button("Scrape Items", use_container_width=True):
        raw_rows: list = []

        if csv_file:
            try:
                df_in = pd.read_csv(csv_file, dtype=str, keep_default_na=False)
                raw_rows.extend(df_in.to_dict(orient="records"))
            except Exception as e:
                st.error(f"Failed reading CSV: {e}")
                return

        if sku_input.strip():
            raw_rows.extend(
                [{"sku": s.strip()} for s in sku_input.splitlines() if s.strip()]
            )

        items = _normalise_rows(raw_rows)

        if not items:
            st.warning("Please provide at least one SKU or URL.")
            return

        indexer = None
        if cms_choice == "Shopify" and origin:
            from scraper.shopify_catalog import ShopifyCatalogIndexer

            @st.cache_resource(ttl=3600, show_spinner="Indexing Shopify Catalog…")
            def get_cached_indexer(url: str):
                idx = ShopifyCatalogIndexer(url)
                _run(idx.fetch_catalog())
                return idx

            try:
                indexer = get_cached_indexer(origin)
                if not indexer.catalog:
                    st.warning("Catalog download blocked (429). Switching to slow search mode.")
                    indexer = None
                else:
                    st.success(f"Using cached catalog ({len(indexer.catalog)} variants)")
            except Exception as e:
                st.error(f"Failed to index catalog: {e}")
                indexer = None

        # ── Live progress tracking ────────────────────────────────────────
        total_items = len(items)
        progress_bar = st.progress(0.0, text=f"Starting {total_items} items…")
        status_line = st.empty()
        error_tally = [0]
        start_ts = [time.monotonic()]

        def on_progress(completed: int, total: int, sku: str, state: str) -> None:
            pct = completed / total if total > 0 else 1.0
            elapsed = time.monotonic() - start_ts[0]
            per_item = elapsed / completed if completed > 0 else 0
            eta_s = int(per_item * (total - completed))
            eta_str = (
                f"~{eta_s}s remaining"
                if completed < total and eta_s > 0
                else "finishing…"
            )
            if state == "failed":
                error_tally[0] += 1
            icon = "✅" if state == "completed" else "❌"
            progress_bar.progress(
                pct,
                text=f"{icon} {completed}/{total} — {eta_str}"
                + (f"  ({error_tally[0]} errors)" if error_tally[0] > 0 else ""),
            )
            if state == "failed":
                status_line.warning(f"❌ **{sku}** — failed (see error column)")
            else:
                status_line.markdown(f"✅ **{sku}** — OK")

        # Rows land here as they are scraped, so a crash, a closed tab or a
        # Streamlit rerun can't discard a part-finished run. Re-running the
        # same origin resumes instead of re-fetching.
        checkpoint_path = _checkpoint_for(origin)
        if fresh_run and os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)

        recovered = _load_checkpoint(checkpoint_path)
        if recovered:
            st.info(
                f"Resuming — {len(recovered)} row(s) from an earlier run are already "
                "saved and will be reused. Tick **Re-fetch everything** to ignore them."
            )

        with st.spinner(""):
            results = _run(
                scrape_items(
                    items,
                    cms_choice,
                    origin,
                    url_pattern,
                    concurrency,
                    delay_ms,
                    indexer=indexer,
                    fast_mode=fast_mode,
                    on_progress=on_progress,
                    checkpoint_path=checkpoint_path,
                )
            )

        progress_bar.empty()
        status_line.empty()

        # Show everything the user asked for, not just what this pass fetched,
        # so a resumed run still exports a complete file.
        if recovered:
            seen = {(r.get("sku") or "") for r in results}
            results = results + [r for r in recovered if (r.get("sku") or "") not in seen]
            order = {(r.get("sku") or ""): i for i, r in enumerate(items)}
            results.sort(key=lambda d: order.get(d.get("sku") or "", len(order)))

        st.session_state["sku_results"] = results

        errors_count = sum(1 for r in results if r.get("error"))
        elapsed_total = time.monotonic() - start_ts[0]
        elapsed_str = f"{elapsed_total:.1f}s"

        if errors_count > 0:
            st.warning(
                f"Completed in {elapsed_str} — ⚠️ {errors_count}/{len(results)} items failed. "
                f"Check the **error** column below for details."
            )
            # Surface a summary of error types to help diagnose
            error_types: dict = {}
            for r in results:
                err = r.get("error") or ""
                key = err.split(":")[0].strip() if err else "unknown"
                error_types[key] = error_types.get(key, 0) + 1
            with st.expander("Error breakdown"):
                for etype, count in sorted(error_types.items(), key=lambda x: -x[1]):
                    st.markdown(f"- **{etype}**: {count} item(s)")
        else:
            st.success(f"Completed in {elapsed_str} — {len(results)} items scraped successfully.")

    if "sku_results" in st.session_state and st.session_state["sku_results"]:
        _render_results(st.session_state["sku_results"], "results.csv")
    elif "sku_results" in st.session_state:
        st.info("No results found.")


if __name__ == "__main__":
    main()
