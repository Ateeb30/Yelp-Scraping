import io
import csv
import time
import requests
import streamlit as st
from scraper import RESULTS_PER_PAGE, build_search_url

st.set_page_config(page_title="Yelp Scraper", layout="centered")
st.title("Yelp Business Scraper")

# ── API URL (entered once, stored in session) ──────────────────────────────────
with st.sidebar:
    st.header("Local API")
    api_url = st.text_input(
        "ngrok URL",
        placeholder="https://xxxx-xx-xx-xx-xx.ngrok-free.app",
        help="Run local_api.py on your machine, then paste the ngrok HTTPS URL here.",
    )

    if api_url:
        try:
            r = requests.get(f"{api_url.rstrip('/')}/ping", timeout=5)
            if r.ok:
                st.success("Connected to local API")
            else:
                st.error(f"API returned {r.status_code}")
        except Exception as e:
            st.error(f"Cannot reach API: {e}")
    else:
        st.info("Paste your ngrok URL to get started.")

# ── Main UI ────────────────────────────────────────────────────────────────────
query    = st.text_input("What are you looking for?", placeholder="e.g. pizza, dentist, plumber")
location = st.text_input("City",                      placeholder="e.g. New York")
pages    = st.number_input("Number of pages", min_value=1, max_value=20, value=3)
st.caption(f"Each page has ~10 results — {pages} pages = up to {int(pages) * RESULTS_PER_PAGE} businesses.")

run = st.button(
    "Scrape",
    type="primary",
    disabled=not (query and location and api_url),
)

if run:
    base = api_url.rstrip("/")
    all_results = []
    status      = st.empty()
    progress    = st.progress(0)
    table_ph    = st.empty()

    # ── Phase 1: collect businesses from search pages ──────────────────────────
    for i in range(int(pages)):
        start = i * RESULTS_PER_PAGE
        status.info(f"Searching page {i + 1} of {pages}…")
        try:
            r = requests.get(
                f"{base}/search",
                params={"query": query, "location": location, "start": start},
                timeout=30,
            )
            r.raise_for_status()
            batch = r.json()
        except Exception as e:
            st.error(f"Failed on page {i + 1}: {e}")
            break

        if not batch:
            status.warning("No results on this page — stopping.")
            break

        all_results.extend(batch)
        progress.progress((i + 1) / (int(pages) * 2))
        time.sleep(1)

    if not all_results:
        st.error("No businesses found. Check your query or make sure local_api.py is running.")
        st.stop()

    # ── Phase 2: fetch phone numbers ──────────────────────────────────────────
    total = len(all_results)
    for idx, biz in enumerate(all_results):
        status.info(f"Getting phone {idx + 1}/{total}: {biz['name']}")
        try:
            r = requests.get(f"{base}/phone", params={"url": biz["url"]}, timeout=20)
            biz["phone"] = r.json().get("phone", "") if r.ok else ""
        except Exception:
            biz["phone"] = ""
        progress.progress(0.5 + (idx + 1) / (total * 2))
        table_ph.dataframe(all_results[: idx + 1], use_container_width=True)
        time.sleep(1)

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
