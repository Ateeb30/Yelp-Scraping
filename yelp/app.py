import io
import csv
import os
import sys
import re
import glob
import time
import subprocess
import urllib.request
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
    "libglib2.0-0t64",
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
    "libasound2t64",
    "libatspi2.0-0",
    "libatspi2.0-0t64",
    "libexpat1",
]

# For packages that aren't in the apt index, fall back to Debian's public pool.
# Scraped from the directory listing — no hardcoded version numbers needed.
_POOL_DIRS = {
    "libatk1.0-0":           "http://deb.debian.org/debian/pool/main/a/atk1.0/",
    "libatk1.0-0t64":        "http://deb.debian.org/debian/pool/main/a/atk1.0/",
    "libatk-bridge2.0-0":    "http://deb.debian.org/debian/pool/main/a/at-spi2-atk/",
    "libatk-bridge2.0-0t64": "http://deb.debian.org/debian/pool/main/a/at-spi2-atk/",
    "libatspi2.0-0":         "http://deb.debian.org/debian/pool/main/a/at-spi2-core/",
    "libatspi2.0-0t64":      "http://deb.debian.org/debian/pool/main/a/at-spi2-core/",
}

_LIB_DIR = "/tmp/pw_libs"
_DEB_DIR = "/tmp/debs"


def _pool_download(pkg_name: str, dest_dir: str) -> tuple[bool, str]:
    """
    Fetch Debian's pool directory listing, find the latest amd64 deb for
    pkg_name, download it to dest_dir.  Returns (success, detail).
    """
    pool_url = _POOL_DIRS.get(pkg_name)
    if not pool_url:
        return False, "no pool URL defined"

    try:
        req = urllib.request.Request(pool_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"listing fetch failed: {e}"

    # Find all amd64 debs for this exact package (prefix match)
    prefix = re.escape(pkg_name) + r"_[^\"]+_amd64\.deb"
    matches = re.findall(prefix, html)
    if not matches:
        return False, f"no amd64 deb found in {pool_url}"

    filename = matches[-1]          # last = newest version in the listing
    download_url = pool_url + filename
    dest = os.path.join(dest_dir, filename)
    try:
        urllib.request.urlretrieve(download_url, dest)
        return True, download_url
    except Exception as e:
        return False, f"download failed: {e}"


@st.cache_resource(show_spinner="Setting up browser (first run only)...")
def install_browsers():
    log = {}

    # ── OS info (helps diagnose future failures) ───────────────────────────────
    try:
        with open("/etc/os-release") as f:
            log["os_release"] = f.read().strip()[:200]
    except Exception:
        log["os_release"] = "n/a"

    os.makedirs(_LIB_DIR, exist_ok=True)
    os.makedirs(_DEB_DIR, exist_ok=True)

    # ── Step 1: try apt-get download first (fast, no root needed) ─────────────
    apt_results = {}
    for pkg in _REQUIRED_PACKAGES:
        r = subprocess.run(
            ["apt-get", "download", pkg],
            cwd=_DEB_DIR, capture_output=True, text=True,
        )
        apt_results[pkg] = r.returncode

    log["apt_results"] = apt_results

    # ── Step 2: for packages apt couldn't find, fetch from Debian pool ─────────
    fallback_results = {}
    for pkg, rc in apt_results.items():
        if rc != 0 and pkg in _POOL_DIRS:
            ok, detail = _pool_download(pkg, _DEB_DIR)
            fallback_results[pkg] = detail if ok else f"FAILED: {detail}"

    log["fallback_results"] = fallback_results

    # ── Step 3: extract every downloaded deb ──────────────────────────────────
    debs = glob.glob(f"{_DEB_DIR}/*.deb")
    extract_results = {}
    for deb in debs:
        r = subprocess.run(
            ["dpkg-deb", "-x", deb, _LIB_DIR],
            capture_output=True, text=True,
        )
        extract_results[os.path.basename(deb)] = r.returncode

    log["extract_results"] = extract_results

    # ── Step 4: set LD_LIBRARY_PATH to the extracted libs ─────────────────────
    so_files = glob.glob(f"{_LIB_DIR}/**/*.so*", recursive=True)
    lib_dirs = list({os.path.dirname(f) for f in so_files})
    if lib_dirs:
        os.environ["LD_LIBRARY_PATH"] = (
            ":".join(lib_dirs) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
        )

    log["so_files_found"]  = len(so_files)
    log["lib_dirs"]        = lib_dirs
    log["LD_LIBRARY_PATH"] = os.environ.get("LD_LIBRARY_PATH", "")

    # ── Step 5: install Playwright's Chromium binary ───────────────────────────
    r_install = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    log["install_rc"]  = r_install.returncode
    log["install_out"] = (r_install.stdout + r_install.stderr)[-400:]

    # ── Step 6: spot-check key libs ───────────────────────────────────────────
    log["atk_files"]  = glob.glob(f"{_LIB_DIR}/**/libatk*.so*", recursive=True)
    log["glib_files"] = glob.glob(f"{_LIB_DIR}/**/libglib-2.0.so*", recursive=True)

    return log


debug = install_browsers()


def cloud_fetch(url: str) -> Selector:
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
