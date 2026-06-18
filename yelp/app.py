import io
import csv
import sys
import time
import subprocess
import streamlit as st

from scraper import (
    build_search_url,
    extract_apollo_state,
    resolve_search_order,
    parse_search_page,
    fetch_phone,
    RESULTS_PER_PAGE,
)

st.set_page_config(page_title="Yelp Scraper", layout="centered")
st.title("Yelp Business Scraper")


@st.cache_resource(show_spinner="Setting up browser (first run only)...")
def install_browsers():
    # Install system dependencies (needs root — works during Streamlit Cloud build)
    subprocess.run(
        [sys.executable, "-m", "playwright", "install-deps", "chromium"],
        capture_output=True,
    )
    # Download the Chromium binary
    subprocess.run(
        [sys.executable, "-m", "patchright", "install", "chromium"],
        capture_output=True,
    )


install_browsers()

query    = st.text_input("What are you looking for?", placeholder="e.g. pizza, dentist, plumber")
location = st.text_input("City", placeholder="e.g. New York")
pages    = st.number_input("Number of pages", min_value=1, max_value=20, value=3)

st.caption(f"Each page has ~10 results — {pages} pages = up to {pages * RESULTS_PER_PAGE} businesses.")

run = st.button("Scrape", type="primary", disabled=not (query and location))

if run:
    from scrapling.fetchers import StealthyFetcher

    all_results = []
    status = st.empty()
    progress = st.progress(0)
    table_placeholder = st.empty()

    # --- Step 1: collect businesses from search pages ---
    for i in range(int(pages)):
        start = i * RESULTS_PER_PAGE
        url = build_search_url(query, location, start)
        status.info(f"Searching page {i + 1} of {pages}...")

        try:
            page = StealthyFetcher.fetch(
                url,
                headless=True,
                network_idle=True,
                google_search=True,
                disable_resources=True,
                timeout=60_000,
            )
            state = extract_apollo_state(page)
            ordered = resolve_search_order(state)
            batch = parse_search_page(state, ordered) if ordered else []
        except Exception as e:
            st.error(f"Failed on page {i + 1}: {e}")
            break

        if not batch:
            status.warning("No results found on this page.")
            break

        all_results.extend(batch)
        progress.progress((i + 1) / (int(pages) * 2))
        time.sleep(2)

    if not all_results:
        st.error("No businesses found. Try a different search or city.")
        st.stop()

    # --- Step 2: fetch phone numbers ---
    total = len(all_results)
    for idx, r in enumerate(all_results):
        status.info(f"Getting phone {idx + 1} of {total}: {r['name']}")
        r["phone"] = fetch_phone(r["url"], use_stealth=True)
        progress.progress(0.5 + (idx + 1) / (total * 2))

        table_placeholder.dataframe(
            [x for x in all_results[:idx + 1]],
            use_container_width=True,
        )
        time.sleep(2)

    progress.progress(1.0)
    status.success(f"Done! Scraped {total} businesses.")
    table_placeholder.dataframe(all_results, use_container_width=True)

    # --- CSV download ---
    buf = io.StringIO()
    fields = ["name", "phone", "location", "url"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_results)

    filename = f"{query.replace(' ', '_')}_{location.replace(' ', '_')}.csv"
    st.download_button(
        label="Download CSV",
        data=buf.getvalue().encode("utf-8"),
        file_name=filename,
        mime="text/csv",
    )
