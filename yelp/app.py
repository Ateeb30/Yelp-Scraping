import io
import csv
import time
import streamlit as st
from scraper import (
    build_search_url, fetch_page, extract_apollo_state,
    resolve_search_order, parse_search_page, fetch_phone,
    RESULTS_PER_PAGE,
)

st.set_page_config(page_title="Yelp Scraper", layout="centered")
st.title("Yelp Business Scraper")

# ── Proxy configuration ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Proxy (required on cloud)")
    proxy = st.text_input(
        "Proxy URL",
        placeholder="http://user:pass@host:port",
        help=(
            "Residential proxy so Yelp doesn't block the request. "
            "Supports http://, https://, socks5://, socks4://. "
            "Leave blank if running locally."
        ),
    )
    if proxy:
        st.success("Proxy configured.")
    else:
        st.warning("No proxy — will only work if running locally.")

# ── Main inputs ────────────────────────────────────────────────────────────────
query    = st.text_input("What are you looking for?", placeholder="e.g. pizza, dentist, plumber")
location = st.text_input("City",                      placeholder="e.g. New York")
pages    = st.number_input("Number of pages", min_value=1, max_value=20, value=3)
st.caption(f"Each page has ~10 results — {pages} pages = up to {int(pages) * RESULTS_PER_PAGE} businesses.")

run = st.button("Scrape", type="primary", disabled=not (query and location))

if run:
    all_results: list[dict] = []
    status   = st.empty()
    progress = st.progress(0)
    table_ph = st.empty()
    proxy_arg = proxy.strip() or None

    # ── Phase 1: collect businesses ────────────────────────────────────────────
    for i in range(int(pages)):
        start = i * RESULTS_PER_PAGE
        url   = build_search_url(query, location, start)
        status.info(f"Searching page {i + 1} of {pages}…")
        try:
            page_sel = fetch_page(url, proxy=proxy_arg)
            state    = extract_apollo_state(page_sel)
            ordered  = resolve_search_order(state)
            batch    = parse_search_page(state, ordered) if ordered else []
        except Exception as e:
            st.error(f"Failed on page {i + 1}: {e}")
            break

        if not batch:
            status.warning("No results on this page — stopping.")
            break

        all_results.extend(batch)
        progress.progress((i + 1) / (int(pages) * 2))
        time.sleep(2)

    if not all_results:
        st.error("No businesses found. Check your query, city, or proxy.")
        st.stop()

    # ── Phase 2: fetch phone numbers ───────────────────────────────────────────
    total = len(all_results)
    for idx, biz in enumerate(all_results):
        status.info(f"Getting phone {idx + 1}/{total}: {biz['name']}")
        biz["phone"] = fetch_phone(biz["url"], proxy=proxy_arg)
        progress.progress(0.5 + (idx + 1) / (total * 2))
        table_ph.dataframe(all_results[: idx + 1], use_container_width=True)
        time.sleep(2)

    progress.progress(1.0)
    status.success(f"Done! Scraped {total} businesses.")
    table_ph.dataframe(all_results, use_container_width=True)

    # ── CSV download ───────────────────────────────────────────────────────────
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["name", "phone", "location", "url"],
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_results)

    st.download_button(
        label="Download CSV",
        data=buf.getvalue().encode("utf-8"),
        file_name=f"{query.replace(' ','_')}_{location.replace(' ','_')}.csv",
        mime="text/csv",
    )
