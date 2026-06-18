#!/usr/bin/env python3
"""
Yelp business listings scraper.
Collects name, phone, location, and URL for each result.

Usage:
    python scraper.py "pizza" "New York, NY"
    python scraper.py "dentist" "Austin, TX" --pages 5 --output dentists.csv
    python scraper.py "coffee" "Chicago, IL" --stealth
"""

import json
import csv
import time
import argparse
from html import unescape
from pathlib import Path

from scrapling.fetchers import Fetcher, StealthyFetcher


RESULTS_PER_PAGE = 10


def build_search_url(query: str, location: str, start: int = 0) -> str:
    q = query.strip().replace(" ", "+")
    loc = location.strip().replace(" ", "+")
    return f"https://www.yelp.com/search?find_desc={q}&find_loc={loc}&start={start}"


def fetch_page(url: str, use_stealth: bool = False):
    if use_stealth:
        return StealthyFetcher.fetch(
            url, headless=True, network_idle=True,
            google_search=True, disable_resources=True, timeout=45_000,
        )
    return Fetcher.get(url, stealthy_headers=True, impersonate="chrome", timeout=20)


def extract_apollo_state(page) -> dict:
    for s in (page.css('script:not([src])') or []):
        text = str(s.text or "")
        if "ROOT_QUERY" not in text:
            continue
        inner = text.strip()
        if inner.startswith("<!--"):
            inner = inner[4:]
        if inner.endswith("-->"):
            inner = inner[:-3]
        try:
            return json.loads(unescape(inner.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


def resolve_search_order(state: dict) -> list[str]:
    root = state.get("ROOT_QUERY", {})
    best: list[str] = []
    for key, value in root.items():
        if not key.startswith("businesses(") or not isinstance(value, list):
            continue
        if len(value) > len(best):
            best = [ref["__ref"].split(":", 1)[1] for ref in value if isinstance(ref, dict) and "__ref" in ref]
    return best


def parse_search_page(state: dict, ordered_encids: list[str]) -> list[dict]:
    results = []
    for encid in ordered_encids:
        biz = state.get(f"Business:{encid}")
        if not isinstance(biz, dict) or not biz.get("name"):
            continue
        loc_ref = (biz.get("location") or {}).get("__ref", "")
        loc = state.get(loc_ref, {}) if loc_ref else {}
        addr = loc.get("address") or {}
        address_line = addr.get("addressLine1", "")
        city = addr.get("city", "")
        alias = biz.get("alias", "")
        results.append({
            "name":     biz.get("name", ""),
            "phone":    "",
            "location": ", ".join(p for p in [address_line, city] if p),
            "url":      f"https://www.yelp.com/biz/{alias}" if alias else "",
        })
    return results


def fetch_phone(biz_url: str, use_stealth: bool = False) -> str:
    try:
        page = fetch_page(biz_url, use_stealth)
        state = extract_apollo_state(page)
        for key, value in state.items():
            if not key.startswith("Business:") or not isinstance(value, dict):
                continue
            # meteredPhoneNumber is what Yelp displays to users on the page
            metered = value.get("meteredPhoneNumber") or {}
            phone = metered.get("phoneText", "")
            if phone:
                return phone
            # fallback: actual business number
            phone_info = value.get("phoneNumber") or {}
            phone = phone_info.get("formatted", "")
            if phone:
                return phone
    except Exception as exc:
        print(f"    [ERROR] {exc}")
    return ""


def scrape_search(url: str, use_stealth: bool = False) -> list[dict]:
    try:
        page = fetch_page(url, use_stealth)
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        return []

    page_text = str(page.text or "").lower()
    if "access denied" in page_text or "captcha" in page_text:
        print("  [WARN] Blocked — try --stealth flag.")
        return []

    state = extract_apollo_state(page)
    if not state:
        print("  [WARN] Apollo state not found.")
        return []

    ordered = resolve_search_order(state)
    if not ordered:
        print("  [WARN] No results list found in Apollo state.")
        return []

    return parse_search_page(state, ordered)


def save_csv(records: list[dict], path: Path) -> None:
    if not records:
        print("\nNo records to save.")
        return
    fields = ["name", "phone", "location", "url"]
    candidate = path
    counter = 1
    while True:
        try:
            with open(candidate, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(records)
            print(f"\nSaved {len(records)} businesses -> {candidate}")
            return
        except PermissionError:
            candidate = path.with_stem(f"{path.stem}_{counter}")
            counter += 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Yelp business listings to CSV")
    ap.add_argument("query",     help='Search keyword  e.g.  "pizza"')
    ap.add_argument("location",  help='City or ZIP     e.g.  "New York, NY"')
    ap.add_argument("--pages",   type=int,   default=3,          help="Pages to scrape (default: 3, ~10 results each)")
    ap.add_argument("--output",  default="results.csv",          help="Output CSV (default: results.csv)")
    ap.add_argument("--delay",   type=float, default=2.0,        help="Seconds between requests (default: 2)")
    ap.add_argument("--stealth", action="store_true",            help="Use stealth browser (slower, harder to block)")
    args = ap.parse_args()

    print(f"\nYelp Scraper")
    print(f"  Query    : {args.query}")
    print(f"  Location : {args.location}")
    print(f"  Pages    : {args.pages}  (up to {args.pages * RESULTS_PER_PAGE} results)")
    print(f"  Output   : {args.output}\n")

    all_results: list[dict] = []

    # Step 1 — collect businesses from search pages
    for i in range(args.pages):
        start = i * RESULTS_PER_PAGE
        url = build_search_url(args.query, args.location, start)
        print(f"[Page {i + 1}/{args.pages}]  {url}")

        batch = scrape_search(url, use_stealth=args.stealth)
        if not batch:
            print("  No results — stopping early.")
            break

        all_results.extend(batch)
        print(f"  Found {len(batch)} businesses (total: {len(all_results)})")

        if i < args.pages - 1:
            time.sleep(args.delay)

    if not all_results:
        return

    # Step 2 — visit each business page to get the phone number
    print(f"\n[Fetching phone numbers for {len(all_results)} businesses]")
    for r in all_results:
        phone = fetch_phone(r["url"], use_stealth=args.stealth)
        r["phone"] = phone
        print(f"  {r['name']:<40} {phone or '(not listed)'}")
        time.sleep(args.delay)

    save_csv(all_results, Path(args.output))


if __name__ == "__main__":
    main()
