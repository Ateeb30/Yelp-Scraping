import io
import csv
import time
import requests as _requests
import streamlit as st
from scraper import (
    build_search_url, fetch_page, extract_apollo_state,
    resolve_search_order, parse_search_page, fetch_phone,
    RESULTS_PER_PAGE,
)

st.set_page_config(page_title="Yelp Scraper", layout="centered")
st.title("Yelp Business Scraper")

# ── Connection mode ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Connection")
    mode = st.radio(
        "Mode",
        ["Local API", "Proxy"],
        help="Local API = run local_api.py on your machine + a tunnel. "
             "Proxy = provide a residential proxy URL.",
    )

    api_url   = ""
    proxy_url = ""

    if mode == "Local API":
        api_url = st.text_input(
            "Tunnel URL",
            placeholder="https://abc123.serveo.net",
            help="Run local_api.py, then: ssh -R 80:localhost:5000 serveo.net",
        )
        if api_url:
            try:
                r = _requests.get(f"{api_url.rstrip('/')}/ping", timeout=5)
                st.success("Connected ✓") if r.ok else st.error(f"API returned {r.status_code}")
            except Exception as e:
                st.error(f"Cannot reach: {e}")
        else:
            st.info("Run local_api.py then paste the tunnel URL.")
    else:
        proxy_url = st.text_input(
            "Proxy URL",
            placeholder="http://user:pass@host:port",
        )
        if proxy_url:
            st.success("Proxy configured.")
        else:
            st.warning("No proxy — requests go via Streamlit Cloud IP.")

# ── Main inputs ────────────────────────────────────────────────────────────────
query    = st.text_input("What are you looking for?", placeholder="e.g. pizza, dentist, plumber")
location = st.text_input("City",                      placeholder="e.g. New York")
pages    = st.number_input("Number of pages", min_value=1, max_value=20, value=3)
st.caption(f"Each page has ~10 results — {pages} pages = up to {int(pages) * RESULTS_PER_PAGE} businesses.")

ready = query and location and (api_url or proxy_url or mode == "Proxy")
run   = st.button("Scrape", type="primary", disabled=not ready)


def _search_page(query, location, start, api_url, proxy_arg):
    if api_url:
        r = _requests.get(
            f"{api_url.rstrip('/')}/search",
            params={"query": query, "location": location, "start": start},
            timeout=30,
        )
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"API returned non-JSON (HTTP {r.status_code}):\n{r.text[:500]}")
        if isinstance(data, dict) and "error" in data:
            raise RuntimeError(f"API error: {data['error']}\n{data.get('trace', '')}")
        return data
    page_sel = fetch_page(build_search_url(query, location, start), proxy=proxy_arg)
    state    = extract_apollo_state(page_sel)
    ordered  = resolve_search_order(state)
    return parse_search_page(state, ordered) if ordered else []


def _get_phone(biz_url, api_url, proxy_arg):
    if api_url:
        r = _requests.get(f"{api_url.rstrip('/')}/phone", params={"url": biz_url}, timeout=20)
        return r.json().get("phone", "") if r.ok else ""
    return fetch_phone(biz_url, proxy=proxy_arg)


if run:
    all_results: list[dict] = []
    proxy_arg = proxy_url.strip() or None
    status    = st.empty()
    progress  = st.progress(0)
    table_ph  = st.empty()

    for i in range(int(pages)):
        status.info(f"Searching page {i + 1} of {pages}…")
        try:
            batch = _search_page(query, location, i * RESULTS_PER_PAGE, api_url, proxy_arg)
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
        st.error("No businesses found. Check your query, city, or connection.")
        st.stop()

    total = len(all_results)
    for idx, biz in enumerate(all_results):
        status.info(f"Getting phone {idx + 1}/{total}: {biz['name']}")
        biz["phone"] = _get_phone(biz["url"], api_url, proxy_arg)
        progress.progress(0.5 + (idx + 1) / (total * 2))
        table_ph.dataframe(all_results[: idx + 1], use_container_width=True)
        time.sleep(2)

    progress.progress(1.0)
    status.success(f"Done! Scraped {total} businesses.")
    table_ph.dataframe(all_results, use_container_width=True)

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
