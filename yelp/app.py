import io
import csv
import os
import sys
import re
import glob
import time
import subprocess
import urllib.request
from urllib.parse import quote
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

_LIB_DIR = "/tmp/pw_libs"
_DEB_DIR = "/tmp/debs"

# Complete map of .so name → Debian/Ubuntu package candidates.
# Both the classic name and the t64 variant are listed so either works.
_SO_TO_PKG = {
    "libglib-2.0.so.0":        ["libglib2.0-0",        "libglib2.0-0t64"],
    "libgmodule-2.0.so.0":     ["libglib2.0-0",        "libglib2.0-0t64"],
    "libgio-2.0.so.0":         ["libglib2.0-0",        "libglib2.0-0t64"],
    "libgobject-2.0.so.0":     ["libglib2.0-0",        "libglib2.0-0t64"],
    "libatk-1.0.so.0":         ["libatk1.0-0",         "libatk1.0-0t64"],
    "libatk-bridge-2.0.so.0":  ["libatk-bridge2.0-0",  "libatk-bridge2.0-0t64"],
    "libatspi.so.0":            ["libatspi2.0-0",       "libatspi2.0-0t64"],
    "libdbus-1.so.3":           ["libdbus-1-3"],
    "libcups.so.2":             ["libcups2"],
    "libdrm.so.2":              ["libdrm2"],
    "libX11.so.6":              ["libx11-6"],
    "libXcomposite.so.1":       ["libxcomposite1"],
    "libXdamage.so.1":          ["libxdamage1"],
    "libXext.so.6":             ["libxext6"],
    "libXfixes.so.3":           ["libxfixes3"],
    "libXrandr.so.2":           ["libxrandr2"],
    "libXrender.so.1":          ["libxrender1"],
    "libXi.so.6":               ["libxi6"],
    "libxcb.so.1":              ["libxcb1"],
    "libxkbcommon.so.0":        ["libxkbcommon0"],
    "libgbm.so.1":              ["libgbm1"],
    "libnspr4.so":              ["libnspr4"],
    "libnss3.so":               ["libnss3"],
    "libnssutil3.so":           ["libnss3"],
    "libsmime3.so":             ["libnss3"],
    "libssl3.so":               ["libnss3"],
    "libplc4.so":               ["libnspr4"],
    "libplds4.so":              ["libnspr4"],
    "libasound.so.2":           ["libasound2",          "libasound2t64"],
    "libexpat.so.1":            ["libexpat1"],
    "libpango-1.0.so.0":        ["libpango-1.0-0"],
    "libpangocairo-1.0.so.0":   ["libpango-1.0-0"],
    "libcairo.so.2":            ["libcairo2"],
    "libz.so.1":                ["zlib1g"],
    "libwoff2dec.so.1.0.2":     ["libwoff1"],
    "libopus.so.0":             ["libopus0"],
    "libwebp.so.7":             ["libwebp7"],
    "libwebpdemux.so.2":        ["libwebpdemux2"],
    "libEGL.so.1":              ["libegl1"],
    "libGLESv2.so.2":           ["libgles2"],
    # glibc — always present, skip
    "libc.so.6":                [],
    "libm.so.6":                [],
    "libpthread.so.0":          [],
    "libdl.so.2":               [],
    "librt.so.1":               [],
    "libresolv.so.2":           [],
    "ld-linux-x86-64.so.2":     [],
    "linux-vdso.so.1":          [],
}

# For packages apt-get can't find, fetch directly from Debian's pool.
_POOL_DIRS = {
    "libatk1.0-0":           "http://deb.debian.org/debian/pool/main/a/atk1.0/",
    "libatk1.0-0t64":        "http://deb.debian.org/debian/pool/main/a/atk1.0/",
    "libatk-bridge2.0-0":    "http://deb.debian.org/debian/pool/main/a/at-spi2-atk/",
    "libatk-bridge2.0-0t64": "http://deb.debian.org/debian/pool/main/a/at-spi2-atk/",
    "libatspi2.0-0":         "http://deb.debian.org/debian/pool/main/a/at-spi2-core/",
    "libatspi2.0-0t64":      "http://deb.debian.org/debian/pool/main/a/at-spi2-core/",
    "libxrender1":           "http://deb.debian.org/debian/pool/main/libx/libxrender/",
    "libxi6":                "http://deb.debian.org/debian/pool/main/libx/libxi/",
    "libx11-6":              "http://deb.debian.org/debian/pool/main/libx/libx11/",
    "libxext6":              "http://deb.debian.org/debian/pool/main/libx/libxext/",
    "libxcb1":               "http://deb.debian.org/debian/pool/main/libx/libxcb/",
    "libxrandr2":            "http://deb.debian.org/debian/pool/main/libx/libxrandr/",
    "libxfixes3":            "http://deb.debian.org/debian/pool/main/libx/libxfixes/",
    "libxdamage1":           "http://deb.debian.org/debian/pool/main/libx/libxdamage/",
    "libxcomposite1":        "http://deb.debian.org/debian/pool/main/libx/libxcomposite/",
    "libxkbcommon0":         "http://deb.debian.org/debian/pool/main/libx/libxkbcommon/",
    "libpango-1.0-0":        "http://deb.debian.org/debian/pool/main/p/pango1.0/",
    "libcairo2":             "http://deb.debian.org/debian/pool/main/c/cairo/",
    "libglib2.0-0":          "http://deb.debian.org/debian/pool/main/g/glib2.0/",
    "libglib2.0-0t64":       "http://deb.debian.org/debian/pool/main/g/glib2.0/",
    "libnspr4":              "http://deb.debian.org/debian/pool/main/n/nspr/",
    "libnss3":               "http://deb.debian.org/debian/pool/main/n/nss/",
    "libdrm2":               "http://deb.debian.org/debian/pool/main/libd/libdrm/",
    "libgbm1":               "http://deb.debian.org/debian/pool/main/m/mesa/",
    "libexpat1":             "http://deb.debian.org/debian/pool/main/e/expat/",
    "libcups2":              "http://deb.debian.org/debian/pool/main/c/cups/",
    "libasound2":            "http://deb.debian.org/debian/pool/main/a/alsa-lib/",
    "libasound2t64":         "http://deb.debian.org/debian/pool/main/a/alsa-lib/",
    "libdbus-1-3":           "http://deb.debian.org/debian/pool/main/d/dbus/",
    "zlib1g":                "http://deb.debian.org/debian/pool/main/z/zlib/",
    "libwoff1":              "http://deb.debian.org/debian/pool/main/w/woff2/",
    "libopus0":              "http://deb.debian.org/debian/pool/main/o/opus/",
    "libwebp7":              "http://deb.debian.org/debian/pool/main/libw/libwebp/",
    "libwebpdemux2":         "http://deb.debian.org/debian/pool/main/libw/libwebp/",
    "libegl1":               "http://deb.debian.org/debian/pool/main/libg/libglvnd/",
    "libgles2":              "http://deb.debian.org/debian/pool/main/libg/libglvnd/",
}


def _pool_download(pkg_name: str, dest_dir: str) -> tuple[bool, str]:
    """
    Fetch Debian's pool directory listing, find the latest amd64 deb for
    pkg_name, and download it to dest_dir.
    """
    pool_url = _POOL_DIRS.get(pkg_name)
    if not pool_url:
        return False, "no pool URL defined"
    try:
        req = urllib.request.Request(pool_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"listing failed: {e}"

    # Extract filenames from href attributes (already URL-encoded, handles epochs like 1:)
    href_pattern = r'href="(' + re.escape(pkg_name) + r'_[^"]+_amd64\.deb)"'
    matches = re.findall(href_pattern, html)
    if not matches:
        # Fallback: search plain text, URL-encode colons (package epochs)
        text_pattern = re.escape(pkg_name) + r"_[^\s\"<>]+_amd64\.deb"
        raw = re.findall(text_pattern, html)
        matches = [m.replace(":", "%3a") for m in raw]
    if not matches:
        return False, f"no amd64 deb in {pool_url}"

    filename = matches[-1]
    download_url = pool_url + filename
    dest = os.path.join(dest_dir, filename.replace("%3a", ":"))
    try:
        urllib.request.urlretrieve(download_url, dest)
        return True, download_url
    except Exception as e:
        return False, f"download failed: {e}"


def _get_missing_libs(binary: str) -> list[str]:
    """Run ldd on binary and return names of all .so files reported as 'not found'."""
    r = subprocess.run(["ldd", binary], capture_output=True, text=True)
    missing = []
    for line in r.stdout.splitlines():
        if "not found" in line:
            so_name = line.strip().split()[0]
            missing.append(so_name)
    return missing


def _download_pkg(pkg: str, dest_dir: str) -> str:
    """Try apt-get download first, fall back to Debian pool. Returns status string."""
    r = subprocess.run(
        ["apt-get", "download", pkg],
        cwd=dest_dir, capture_output=True, text=True,
    )
    if r.returncode == 0:
        return "apt-ok"
    if pkg in _POOL_DIRS:
        ok, detail = _pool_download(pkg, dest_dir)
        return detail if ok else f"FAILED: {detail}"
    return f"skip (no pool URL, apt rc={r.returncode})"


@st.cache_resource(show_spinner="Setting up browser (first run only)...")
def install_browsers():
    log = {}
    os.makedirs(_LIB_DIR, exist_ok=True)
    os.makedirs(_DEB_DIR, exist_ok=True)

    # ── Phase 1: install Playwright's Chromium binary (download only, no libs needed) ──
    r_install = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    log["install_rc"]  = r_install.returncode
    log["install_out"] = (r_install.stdout + r_install.stderr)[-200:]

    # ── Phase 2: find the binary and use ldd to list ALL missing .so files ─────
    chrome_bins = glob.glob(
        os.path.expanduser("~/.cache/ms-playwright/**/chrome-headless-shell"),
        recursive=True,
    )
    if not chrome_bins:
        log["error"] = "chromium binary not found after install"
        return log

    chrome_bin = chrome_bins[0]
    log["chrome_bin"] = chrome_bin

    missing_so = _get_missing_libs(chrome_bin)
    log["missing_so_initial"] = missing_so

    # ── Phase 3: resolve .so names → package names ─────────────────────────────
    pkgs_needed: list[str] = []
    unknown_so: list[str] = []
    for so in missing_so:
        candidates = _SO_TO_PKG.get(so)
        if candidates is None:
            unknown_so.append(so)
        else:
            pkgs_needed.extend(candidates)

    pkgs_needed = list(dict.fromkeys(pkgs_needed))   # dedup, preserve order
    log["packages_needed"] = pkgs_needed
    log["unknown_so"]      = unknown_so              # libs with no known mapping

    # ── Phase 4: download all needed packages ──────────────────────────────────
    dl_results = {pkg: _download_pkg(pkg, _DEB_DIR) for pkg in pkgs_needed}
    log["dl_results"] = dl_results

    # ── Phase 5: extract every downloaded deb ─────────────────────────────────
    debs = glob.glob(f"{_DEB_DIR}/*.deb")
    for deb in debs:
        subprocess.run(["dpkg-deb", "-x", deb, _LIB_DIR], capture_output=True)

    # ── Phase 6: set LD_LIBRARY_PATH ──────────────────────────────────────────
    so_files = glob.glob(f"{_LIB_DIR}/**/*.so*", recursive=True)
    lib_dirs = list({os.path.dirname(f) for f in so_files})
    if lib_dirs:
        os.environ["LD_LIBRARY_PATH"] = (
            ":".join(lib_dirs) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
        )
    log["so_files_found"]  = len(so_files)
    log["LD_LIBRARY_PATH"] = os.environ.get("LD_LIBRARY_PATH", "")

    # ── Phase 7: spot-check key files are actually on disk ────────────────────
    log["xi_files"]    = glob.glob(f"{_LIB_DIR}/**/libXi*",     recursive=True)
    log["xrender_files"] = glob.glob(f"{_LIB_DIR}/**/libXrender*", recursive=True)

    # Force pool download for libXi if it's somehow still absent (belt + suspenders)
    if not log["xi_files"]:
        ok, detail = _pool_download("libxi6", _DEB_DIR)
        log["xi6_force_dl"] = detail if ok else f"FAILED: {detail}"
        if ok:
            for deb in glob.glob(f"{_DEB_DIR}/libxi6*.deb"):
                subprocess.run(["dpkg-deb", "-x", deb, _LIB_DIR], capture_output=True)
            log["xi_files"] = glob.glob(f"{_LIB_DIR}/**/libXi*", recursive=True)
            # Refresh LD_LIBRARY_PATH with any new dirs
            so_files2 = glob.glob(f"{_LIB_DIR}/**/*.so*", recursive=True)
            lib_dirs2  = list({os.path.dirname(f) for f in so_files2})
            os.environ["LD_LIBRARY_PATH"] = (
                ":".join(lib_dirs2) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
            )

    # ── Phase 8: verify — run ldd again with updated PATH ─────────────────────
    still_missing = _get_missing_libs(chrome_bin)
    log["still_missing_so"] = still_missing

    # Quick smoke-test: try launching chrome and immediately killing it
    r_smoke = subprocess.run(
        [chrome_bin, "--headless", "--no-sandbox", "--disable-gpu",
         "--dump-dom", "about:blank"],
        capture_output=True, text=True, timeout=10,
    )
    log["smoke_rc"]  = r_smoke.returncode
    log["smoke_err"] = r_smoke.stderr[:300]

    return log


debug = install_browsers()


_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--single-process",              # browser + renderer in one process, saves ~100 MB
    "--no-first-run",
    "--disable-blink-features=AutomationControlled",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-extensions",
    "--mute-audio",
    "--hide-scrollbars",
]

# Block resource types that aren't needed to parse Apollo state from the HTML.
# This prevents Chrome from downloading ~3 MB of images/fonts that OOM the container.
_BLOCK_TYPES = {"image", "media", "font", "stylesheet", "other"}


def _block_heavy(route):
    if route.request.resource_type in _BLOCK_TYPES:
        route.abort()
    else:
        route.continue_()


def cloud_fetch(url: str) -> Selector:
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=_CHROME_ARGS)
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
                page.route("**/*", _block_heavy)   # block images/fonts/css
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                content = page.content()           # Apollo state is in the initial HTML
                browser.close()
            return Selector(content)
        except Exception as exc:
            last_err = exc
            time.sleep(3)
    raise last_err  # type: ignore[misc]


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
            html_str   = str(page.html or "")
            has_root   = "ROOT_QUERY" in html_str
            title_els  = page.css("title")
            page_title = title_els[0].text.strip() if title_els else "(no title)"
            st.warning(
                f"No Apollo state on page {i+1}.  "
                f"**Title:** `{page_title[:120]}`  |  "
                f"**ROOT_QUERY in HTML:** `{has_root}`  |  "
                f"**HTML size:** {len(html_str)} chars"
            )
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
