import datetime
import os
import sys
import threading

import requests
from flask import Flask, jsonify, render_template, request

from updater import check_for_update, download_and_apply_update
from version import __version__

API_URL = "https://json.tarkov.dev/pve/items"
MIN_PROFIT = 2000
LISTING_FEE_ESTIMATE = 2500
EXCLUDED_TYPES = {"gun", "preset"}
FILTER_WORDS = ["default"]


def resource_path(relative_path):
    # PyInstaller unpacks bundled data files into sys._MEIPASS at runtime;
    # fall back to the script's own directory when running unfrozen.
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def fetch_items():
    response = requests.get(
        API_URL,
        headers={"User-Agent": "EFT-Flea/1.0 (contact: you@example.com)"},
    )
    response.raise_for_status()
    return response.json()["data"]["items"]


def display_name(normalized_name):
    return normalized_name.replace("-", " ").title()


def name_contains_filtered_word(name, filter_words):
    name_lower = name.lower()
    return any(word in name_lower for word in filter_words)


def compute_profitable_items():
    items = fetch_items()
    profitable_items = []

    for item_id, item in items.items():
        normalized_name = item.get("normalizedName")
        if not normalized_name:
            continue

        item_types = set(item.get("types") or [])
        if item_types & EXCLUDED_TYPES:
            continue

        name = display_name(normalized_name)
        if name_contains_filtered_word(name, FILTER_WORDS):
            continue

        flea_price = item.get("lastLowPrice")
        if not flea_price:
            continue

        trader_max = 0
        for entry in item.get("sellToTrader") or []:
            price = entry.get("priceRUB") or 0
            if price > trader_max:
                trader_max = price

        if trader_max == 0:
            continue

        profit = trader_max - flea_price
        if profit <= MIN_PROFIT:
            continue

        buy_till_rub = trader_max - LISTING_FEE_ESTIMATE
        profitable_items.append({
            "name": name,
            "profit": profit,
            "flea": flea_price,
            "rub": int(buy_till_rub),
            "usd": int(buy_till_rub / 163),
            "eur": int(buy_till_rub / 190),
        })

    profitable_items.sort(key=lambda entry: entry["profit"], reverse=True)
    return profitable_items


def create_app():
    flask_app = Flask(__name__, template_folder=resource_path("templates"))

    @flask_app.get("/")
    def index():
        return render_template("index.html")

    @flask_app.get("/api/items")
    def api_items():
        try:
            items = compute_profitable_items()
            return jsonify({
                "ok": True,
                "items": items,
                "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except requests.exceptions.RequestException as exc:
            return jsonify({"ok": False, "error": f"Network error: {exc}"}), 502
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @flask_app.get("/api/update-check")
    def api_update_check():
        return jsonify({
            "current_version": __version__,
            "update": check_for_update(),
        })

    @flask_app.post("/api/update-apply")
    def api_update_apply():
        download_url = request.get_json()["download_url"]
        # Runs in a background thread so the HTTP response can flush before
        # the process exits as part of the update swap.
        threading.Thread(
            target=download_and_apply_update, args=(download_url,), daemon=True
        ).start()
        return jsonify({"ok": True})

    return flask_app


# Module-level app object, so standard WSGI servers (gunicorn, Azure App
# Service) can target it directly as "app:app".
app = create_app()

if __name__ == "__main__":
    # Plain local web server (also what Azure runs, just behind gunicorn).
    app.run(host="0.0.0.0", port=5000)
