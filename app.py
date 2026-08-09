import datetime
import json
import math
import os
import sys
import threading
import time

import requests
from flask import Flask, jsonify, render_template, request

from traders import is_included_trader, trader_display_name
from updater import check_for_update, download_and_apply_update
from version import __version__

VALID_GAME_MODES = {"regular", "pve", "pvp-season"}
DEFAULT_GAME_MODE = "pve"
LISTING_FEE_ESTIMATE = 2500

# Guns/presets: excluded from both directions - their price is unreliable
# regardless of which mods happen to be attached to a given listing.
EXCLUDED_TYPES = {"gun", "preset"}

# Weapon mods: excluded from Trader->Flea only. High offer counts don't
# reliably mean a mod actually sells - cheap/junk mods pile up on the flea
# just as easily as ones in real demand, so this side needs the extra
# exclusion. Not relevant for Flea->Trader, where you never depend on the
# item selling on flea at all.
TRADER_TO_FLEA_EXCLUDED_TYPES = EXCLUDED_TYPES | {"mods"}

FILTER_WORDS = ["default"]

# An item with fewer than this many active flea listings is treated as
# untradeable right now - its cached lastLowPrice/avg24hPrice may be stale
# history rather than something you can actually act on.
MIN_OFFER_COUNT = 5

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


def settings_path():
    # Per-user AppData, not next to the exe - survives updates/redownloads
    # and matches where a real Windows app is expected to keep its config.
    base_dir = os.getenv("APPDATA") or os.path.expanduser("~")
    settings_dir = os.path.join(base_dir, "EFT-Flea")
    os.makedirs(settings_dir, exist_ok=True)
    return os.path.join(settings_dir, "settings.json")


def load_settings():
    try:
        with open(settings_path(), "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data):
    with open(settings_path(), "w") as f:
        json.dump(data, f)


def fetch_items(game_mode=DEFAULT_GAME_MODE):
    if game_mode not in VALID_GAME_MODES:
        game_mode = DEFAULT_GAME_MODE
    response = requests.get(
        f"https://json.tarkov.dev/{game_mode}/items",
        headers={"User-Agent": "EFT-Flea/1.0 (contact: you@example.com)"},
    )
    response.raise_for_status()
    return response.json()["data"]["items"]


def display_name(normalized_name):
    return normalized_name.replace("-", " ").title()


def name_contains_filtered_word(name, filter_words):
    name_lower = name.lower()
    return any(word in name_lower for word in filter_words)


def conservative_flea_price(item, side):
    """Picks the less-favorable-to-the-player of lastLowPrice/avg24hPrice,
    so a stale/outlier price in either direction can't inflate a profit
    estimate. side="buy" (you're paying it, e.g. Flea->Trader) takes the
    higher of the two; side="sell" (you're receiving it, e.g. selling on
    flea) takes the lower."""
    candidates = [
        p for p in (item.get("lastLowPrice"), item.get("avg24hPrice")) if p
    ]
    if not candidates:
        return None
    return max(candidates) if side == "buy" else min(candidates)


def compute_profitable_items(game_mode=DEFAULT_GAME_MODE):
    items = fetch_items(game_mode)
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

        # No offer-count/conservative-pricing gate here: you're only ever
        # the buyer on this side, and trader sales are instant/guaranteed,
        # so any currently-listed profitable item is actionable as-is.
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
            "profit": int(profit),
            "flea": int(flea_price),
            "rub": int(buy_till_rub),
            "usd": int(buy_till_rub / 163),
            "eur": int(buy_till_rub / 190),
        })

    profitable_items.sort(key=lambda entry: entry["profit"], reverse=True)
    return profitable_items


def compute_trader_to_flea_items(game_mode=DEFAULT_GAME_MODE):
    """Returns, per item, every trader offer that's profitable on its own -
    not just the single cheapest one - so the client can pick whichever
    offer is actually accessible once a trader-level filter is applied,
    without needing to re-fetch from the backend."""
    items = fetch_items(game_mode)
    result_items = []

    for item_id, item in items.items():
        normalized_name = item.get("normalizedName")
        if not normalized_name:
            continue

        item_types = set(item.get("types") or [])
        if item_types & TRADER_TO_FLEA_EXCLUDED_TYPES:
            continue

        name = display_name(normalized_name)
        if name_contains_filtered_word(name, FILTER_WORDS):
            continue

        if (item.get("lastOfferCount") or 0) < MIN_OFFER_COUNT:
            continue

        offers = [
            o for o in (item.get("buyFromTrader") or [])
            if o.get("priceRUB") and is_included_trader(o.get("trader"))
        ]
        if not offers:
            continue

        base_price = item.get("basePrice")
        flea_sell_price = conservative_flea_price(item, "sell")
        if not base_price or not flea_sell_price:
            continue

        fee = compute_flea_fee(base_price, flea_sell_price)
        net_received = flea_sell_price - fee

        offer_list = []
        for offer in offers:
            buy_price = offer["priceRUB"]
            profit = net_received - buy_price
            if profit <= 0:
                continue

            buy_limit = offer.get("buyLimit") or 0
            offer_list.append({
                "trader": trader_display_name(offer.get("trader")),
                "traderLevel": offer.get("minTraderLevel"),
                "buyPrice": int(buy_price),
                "profit": int(profit),
                "buyLimit": buy_limit,
                "profitPerReset": int(profit * buy_limit) if buy_limit else None,
                "taskLocked": bool(offer.get("taskUnlock")),
            })

        if not offer_list:
            continue

        result_items.append({
            "name": name,
            "fleaSell": int(flea_sell_price),
            "fee": int(fee),
            "offers": offer_list,
        })

    return result_items


def create_app():
    flask_app = Flask(__name__, template_folder=resource_path("templates"))

    @flask_app.get("/")
    def index():
        return render_template("index.html", version=__version__)

    @flask_app.get("/api/items")
    def api_items():
        game_mode = request.args.get("mode", DEFAULT_GAME_MODE)
        try:
            items = compute_profitable_items(game_mode)
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
        game_mode = request.args.get("mode", DEFAULT_GAME_MODE)
        try:
            items = compute_trader_to_flea_items(game_mode)
            return jsonify({
                "ok": True,
                "items": items,
                "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except requests.exceptions.RequestException as exc:
            return jsonify({"ok": False, "error": f"Network error: {exc}"}), 502
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @flask_app.get("/api/settings")
    def api_get_settings():
        return jsonify(load_settings())

    @flask_app.post("/api/settings")
    def api_save_settings():
        save_settings(request.get_json() or {})
        return jsonify({"ok": True})

    @flask_app.get("/api/update-check")
    def api_update_check():
        return jsonify({
            "current_version": __version__,
            "update": check_for_update(),
        })

    @flask_app.post("/api/update-apply")
    def api_update_apply():
        download_url = request.get_json()["download_url"]
        try:
            download_and_apply_update(download_url)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

        # Exit on a short delay from a background thread, so this response
        # actually reaches the client first instead of the process dying
        # mid-request. The app deliberately does not relaunch itself - see
        # download_and_apply_update's docstring for why.
        def delayed_exit():
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=delayed_exit, daemon=True).start()
        return jsonify({
            "ok": True,
            "message": "Update installed. Please close and reopen the app.",
        })

    return flask_app


# Module-level app object, so standard WSGI servers (gunicorn, Azure App
# Service) can target it directly as "app:app".
app = create_app()

if __name__ == "__main__":
    # Plain local web server (also what Azure runs, just behind gunicorn).
    app.run(host="0.0.0.0", port=5000)
