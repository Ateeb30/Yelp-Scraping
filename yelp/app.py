import io
import csv
import os
import sys
import glob
import time
import subprocess
import streamlit as st
from playwright.sync_api import sync_playwright
from scrapling.parser import Selector

from scraper import (
    build_search_url,
    extract_apollo_state,
    resolve_search_order,
    parse_search_page,
    RESULTS_PER_PAGE,
)

# Packages that Playwright's Chromium headless shell needs on a minimal Debian/Ubuntu image.
# apt-get download requires no root — it just fetches the .deb to a local dir.
# dpkg-deb -x also requires no root — it just extracts files to a local dir.
_REQUIRED_PACKAGES = [
    "libglib2.0-0",
    "libglib2.0-0t64",   # Ubuntu 24.04 renamed it
    "libnss3",
    "libnspr4",
    "libdbus-1-3",
    "libatk1.0-0",
    "libatk1.0-0t64",
    "libatk-bridge2.0-0",
    "libatk-bridge2.0-0t64",
    "libcups2",
    "libdrm2",
    "libxkbcommon0",
    "libxcomposite1",
    "libxdamage1",
    "libxfixes3",
    "libxrandr2",
    "libgbm1",
    "libasound2",
    "libasound2t64",     # Ubuntu 24.04 renamed it
    "libatspi2.0-0",
    "libatspi2.0-0t64",
    "libexpat1",
]

_LIB_DIR = "/tmp/pw_libs"


@st.cache_resource(show_spinner="Setting up browser (first run only)...")
def install_browsers():
    log = {}

    # ── Step 1: download missing system libs without root ──────────────────────
    os.makedirs(_LIB_DIR, exist_ok=True)
    os.makedirs("/tmp/debs", exist_ok=True)

    dl_results = {}
    for pkg in _REQUIRED_PACKAGES:
        r = subprocess.run(
            ["apt-get", "download", pkg],
            cwd="/tmp/debs",
            capture_output=True, text=True,
        )
        dl_results[pkg] = r.returncode

    log["download_results"] = dl_results

    # ── Step 2: extract all downloaded debs ────────────────────────────────────
    debs = glob.glob("/tmp/debs/*.deb")
    extract_results = {}
    for deb in debs:
        r = subprocess.run(
            ["dpkg-deb", "-x", deb, _LIB_DIR],
            capture_output=True, text=True,
        )
        extract_results[os.path.basename(deb)] = r.returncode

    log["extract_results"] = extract_results

    # ── Step 3: point LD_LIBRARY_PATH at the extracted shared objects ──────────
    so_files = glob.glob(f"{_LIB_DIR}/**/*.so*", recursive=True)
    lib_dirs  = list({os.path.dirname(f) for f in so_files})
    if lib_dirs:
        os.environ["LD_LIBRARY_PATH"] = ":".join(lib_dirs) + ":" + os.environ.get("LD_LIBRARY_PATH", "")

    log["so_files_found"] = len(so_files)
    log["lib_dirs"]       = lib_dirs
    log["LD_LIBRARY_PATH"] = os.environ.get("LD_LIBRARY_PATH", "")

    # ── Step 4: install Playwright's Chromium binary ───────────────────────────
    r_install = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    log["install_rc"]  = r_install.returncode
    log["install_out"] = (r_install.stdout + r_install.stderr)[-400:]

    # ── Step 5: verify glib is now findable ────────────────────────────────────
    glib_files = glob.glob(f"{_LIB_DIR}/**/libglib-2.0.so*", recursive=True)
    log["glib_files"] = glib_files

    return log


debug = install_browsers()


def cloud_fetch(url: str) -> Selector:
    env = os.environ.copy()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Referer": "https://www.google.com/"},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle", timeout=60_000)
        content = page.content()
        browser.close()
    return Selector(content)


def fetch_phone_cloud(biz_url: str) -> str:
    try:
        page = cloud_fetch(biz_url)
        state = extract_apollo_state(page)
        for key, value in state.items():
            if not key.startswith("Business:") or not isinstance(value, dict):
                continue
            metered = value.get("meteredPhoneNumber") or {}
            phone = metered.get("phoneText", "")
            if phone:
                return phone
            phone_info = value.get("phoneNumber") or {}
            phone = phone_info.get("formatted", "")
            if phone:
                return phone
    except Exception as e:
        st.warning(f"Could not get phone: {e}")
    return ""


st.set_page_config(page_title="Yelp Scraper", layout="centered")
st.title("Yelp Business Scraper")

with st.expander("Browser setup debug info"):
    st.json(debug)

query    = st.text_input("What are you looking for?", placeholder="e.g. pizza, dentist, plumber")
location = st.text_input("City", placeholder="e.g. New York")
pages    = st.number_input("Number of pages", min_value=1, max_value=20, value=3)
st.caption(f"Each page has ~10 results — {pages} pages = up to {pages * RESULTS_PER_PAGE} businesses.")

run = st.button("Scrape", type="primary", disabled=not (query and location))

if run:
    all_results = []
    status = st.empty()
    progress = st.progress(0)
    table_placeholder = st.empty()

    for i in range(int(pages)):
        start = i * RESULTS_PER_PAGE
        url = build_search_url(query, location, start)
        status.info(f"Searching page {i + 1} of {pages}...")
        try:
            page = cloud_fetch(url)
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

    total = len(all_results)
    for idx, r in enumerate(all_results):
        status.info(f"Getting phone {idx + 1} of {total}: {r['name']}")
        r["phone"] = fetch_phone_cloud(r["url"])
        progress.progress(0.5 + (idx + 1) / (total * 2))
        table_placeholder.dataframe([x for x in all_results[:idx + 1]], use_container_width=True)
        time.sleep(2)

    progress.progress(1.0)
    status.success(f"Done! Scraped {total} businesses.")
    table_placeholder.dataframe(all_results, use_container_width=True)

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
