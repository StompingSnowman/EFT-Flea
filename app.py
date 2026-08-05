import datetime
import math
import os
import sys

import requests
from flask import Flask, jsonify, render_template, request

from traders import trader_display_name
from updater import check_for_update, download_and_apply_update
from version import __version__

API_URL = "https://json.tarkov.dev/pve/items"
LISTING_FEE_ESTIMATE = 2500
EXCLUDED_TYPES = {"gun", "preset"}
FILTER_WORDS = ["default"]

# Escape from Tarkov's flea market listing fee formula:
#   Fee = VO * Ti * 4^PO + VR * Tr * 4^PR
# where VO = base price, VR = listing price, and whichever of PO/PR points
# "away from" base price gets an extra ^1.08 exponent. Ti/Tr fixed at 0.05
# per explicit choice (no Intelligence Center / Hideout Management discount
# modeled - this is the raw, undiscounted fee).
FLEA_FEE_TAX = 0.05
FLEA_FEE_BASE = 4
FLEA_FEE_EXPONENT_BOOST = 1.08


def compute_flea_fee(base_price, listing_price):
    if not base_price or not listing_price or base_price <= 0 or listing_price <= 0:
        return 0

    if listing_price < base_price:
        po = math.log10(base_price / listing_price) ** FLEA_FEE_EXPONENT_BOOST
        pr = math.log10(listing_price / base_price)
    else:
        po = math.log10(base_price / listing_price)
        pr = math.log10(listing_price / base_price) ** FLEA_FEE_EXPONENT_BOOST

    return (
        base_price * FLEA_FEE_TAX * (FLEA_FEE_BASE ** po)
        + listing_price * FLEA_FEE_TAX * (FLEA_FEE_BASE ** pr)
    )


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
        if profit <= 0:
            # Not profitable at all - the real minimum-profit threshold is
            # applied client-side so it can be adjusted live without
            # re-fetching from the upstream API.
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


def compute_trader_to_flea_items():
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

        offers = [o for o in (item.get("buyFromTrader") or []) if o.get("priceRUB")]
        if not offers:
            continue

        # Cheapest way to acquire the item, mirroring how the Flea->Trader
        # side picks the single best (highest) trader payout.
        best_offer = min(offers, key=lambda o: o["priceRUB"])
        buy_price = best_offer["priceRUB"]

        base_price = item.get("basePrice")
        flea_sell_price = item.get("lastLowPrice")
        if not base_price or not flea_sell_price:
            continue

        fee = compute_flea_fee(base_price, flea_sell_price)
        net_received = flea_sell_price - fee
        profit = net_received - buy_price
        if profit <= 0:
            continue

        buy_limit = best_offer.get("buyLimit") or 0
        profit_per_reset = int(profit * buy_limit) if buy_limit else None

        profitable_items.append({
            "name": name,
            "trader": trader_display_name(best_offer.get("trader")),
            "traderLevel": best_offer.get("minTraderLevel"),
            "buyPrice": int(buy_price),
            "fleaSell": int(flea_sell_price),
            "fee": int(fee),
            "profit": int(profit),
            "buyLimit": buy_limit,
            "profitPerReset": profit_per_reset,
            "taskLocked": bool(best_offer.get("taskUnlock")),
        })

    profitable_items.sort(key=lambda entry: entry["profit"], reverse=True)
    return profitable_items


def create_app():
    flask_app = Flask(__name__, template_folder=resource_path("templates"))

    @flask_app.get("/")
    def index():
        return render_template("index.html", version=__version__)

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

    @flask_app.get("/api/trader-to-flea")
    def api_trader_to_flea():
        try:
            items = compute_trader_to_flea_items()
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
        # Runs synchronously: on success this calls os._exit(0) itself and
        # never returns. On failure (e.g. a truncated download) it raises,
        # which reaches the client as a real error instead of silently
        # dying in a background thread and leaving the UI stuck on
        # "Updating..." forever.
        try:
            download_and_apply_update(download_url)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return flask_app


# Module-level app object, so standard WSGI servers (gunicorn, Azure App
# Service) can target it directly as "app:app".
app = create_app()

if __name__ == "__main__":
    # Plain local web server (also what Azure runs, just behind gunicorn).
    app.run(host="0.0.0.0", port=5000)
