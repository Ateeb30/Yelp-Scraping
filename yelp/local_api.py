"""
Run this on your local machine while using the Streamlit app.
It proxies all Yelp requests through your residential IP.

Usage:
    python local_api.py
Then in another terminal:
    ngrok http 5000
Paste the ngrok HTTPS URL into the Streamlit app's API URL box.
"""

from flask import Flask, jsonify, request
from scraper import (
    build_search_url, fetch_page, extract_apollo_state,
    resolve_search_order, parse_search_page, fetch_phone,
)

app = Flask(__name__)


@app.route("/search")
def search():
    try:
        query    = request.args.get("query", "")
        location = request.args.get("location", "")
        start    = int(request.args.get("start", 0))
        if not query or not location:
            return jsonify({"error": "query and location are required"}), 400
        url   = build_search_url(query, location, start)
        page  = fetch_page(url)
        state = extract_apollo_state(page)
        order = resolve_search_order(state)
        results = parse_search_page(state, order) if order else []
        return jsonify(results)
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/phone")
def phone():
    try:
        biz_url = request.args.get("url", "")
        if not biz_url:
            return jsonify({"phone": ""})
        return jsonify({"phone": fetch_phone(biz_url)})
    except Exception as e:
        return jsonify({"phone": "", "error": str(e)}), 500


@app.route("/ping")
def ping():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(port=5000)
