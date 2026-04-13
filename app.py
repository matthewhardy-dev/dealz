import os
import json
from flask import Flask, render_template, request, jsonify
from database import db, init_db, Deal, PriceHistory, User, PriceAlert, SavedSearch, Wishlist, cache_deals, get_price_history, check_alerts
from notifications import process_alerts
from smartscraper.deals import (
    scrape_search_deals, scrape_goldbox_deals, get_deal_pages,
    scrape_ebay_deals, scrape_ebay_search_deals, EBAY_DEAL_PAGES,
    scrape_multi_store_deals, deep_scan_deals,
    scrape_amazon_comprehensive, scrape_ebay_comprehensive,
)

app = Flask(__name__)
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# Initialize database
init_db(app)


def _get_filters(data):
    """Extract filter params from request data."""
    filters = {}
    if data.get("min_price"):
        filters["min_price"] = float(data["min_price"])
    if data.get("max_price"):
        filters["max_price"] = float(data["max_price"])
    if data.get("min_discount"):
        filters["min_discount"] = int(data["min_discount"])
    if data.get("condition") and data["condition"] != "any":
        filters["condition"] = data["condition"]
    if data.get("must_contain"):
        words = [w.strip() for w in data["must_contain"].split(",") if w.strip()]
        if words:
            filters["must_contain"] = words
    if data.get("exclude"):
        words = [w.strip() for w in data["exclude"].split(",") if w.strip()]
        if words:
            filters["exclude"] = words
    return filters or None


def _cache_and_alert(deals, query="", source=""):
    """Cache deals and check for triggered alerts."""
    try:
        cache_deals(deals, query, source)
        triggered = check_alerts(deals)
        if triggered:
            process_alerts(triggered)
    except Exception as e:
        print(f"[Cache/Alert error] {e}")


# ============================================================
# Pages
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


# ============================================================
# Deal Search Endpoints
# ============================================================

@app.route("/api/deals/search", methods=["POST"])
def api_deals_search():
    data = request.json
    query = data.get("query", "deals").strip()
    max_results = data.get("max_results", 30)
    sort_by = data.get("sort_by", "cheapest")
    filters = _get_filters(data)
    try:
        if max_results > 60:
            deals = scrape_amazon_comprehensive(query, max_results, filters=filters, sort_by=sort_by)
        else:
            deals = scrape_search_deals(query, max_results, filters=filters, sort_by=sort_by)
        _cache_and_alert(deals, query, "amazon")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/goldbox", methods=["POST"])
def api_deals_goldbox():
    data = request.json or {}
    sort_by = data.get("sort_by", "cheapest")
    filters = _get_filters(data)
    try:
        deals = scrape_goldbox_deals(filters=filters, sort_by=sort_by)
        _cache_and_alert(deals, "goldbox", "amazon")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/pages", methods=["GET"])
def api_deal_pages():
    return jsonify(get_deal_pages())


@app.route("/api/deals/ebay", methods=["POST"])
def api_ebay_deals():
    data = request.json
    page = data.get("page", "daily_deals")
    url = EBAY_DEAL_PAGES.get(page, EBAY_DEAL_PAGES["daily_deals"])
    sort_by = data.get("sort_by", "cheapest")
    filters = _get_filters(data)
    try:
        deals = scrape_ebay_deals(url, max_results=data.get("max_results", 30), filters=filters, sort_by=sort_by)
        _cache_and_alert(deals, page, "ebay")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/ebay/search", methods=["POST"])
def api_ebay_search():
    data = request.json
    query = data.get("query", "deals").strip()
    sort_by = data.get("sort_by", "cheapest")
    max_results = data.get("max_results", 30)
    filters = _get_filters(data)
    try:
        if max_results > 60:
            deals = scrape_ebay_comprehensive(query, max_results, filters=filters, sort_by=sort_by)
        else:
            deals = scrape_ebay_search_deals(query, max_results=max_results, filters=filters, sort_by=sort_by)
        _cache_and_alert(deals, query, "ebay")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/google", methods=["POST"])
def api_google_deals():
    data = request.json
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Search term required"}), 400
    sort_by = data.get("sort_by", "cheapest")
    filters = _get_filters(data)
    try:
        deals = scrape_multi_store_deals(query, max_results=data.get("max_results", 30), filters=filters, sort_by=sort_by)
        _cache_and_alert(deals, query, "slickdeals")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/deepscan", methods=["POST"])
def api_deep_scan():
    data = request.json or {}
    query = data.get("query", "").strip()
    min_discount = int(data.get("min_discount", 0))
    max_results = int(data.get("max_results", 200))
    sort_by = data.get("sort_by", "cheapest")
    try:
        deals = deep_scan_deals(query=query, min_discount=min_discount, max_results=max_results, sort_by=sort_by)
        _cache_and_alert(deals, query, "deepscan")
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# User Account Endpoints
# ============================================================

@app.route("/api/user/register", methods=["POST"])
def api_user_register():
    data = request.json or {}
    email = data.get("email", "").strip()
    phone = data.get("phone", "").strip()
    name = data.get("name", "").strip()
    if not email and not phone:
        return jsonify({"error": "Email or phone number required"}), 400
    # Check if user exists
    existing = None
    if email:
        existing = User.query.filter_by(email=email).first()
    if not existing and phone:
        existing = User.query.filter_by(phone=phone).first()
    if existing:
        # Update existing user
        if email:
            existing.email = email
        if phone:
            existing.phone = phone
        if name:
            existing.name = name
        db.session.commit()
        return jsonify({"user": existing.to_dict(), "message": "Welcome back!"})
    # Create new user
    user = User(email=email or None, phone=phone or None, name=name or None)
    db.session.add(user)
    db.session.commit()
    return jsonify({"user": user.to_dict(), "message": "Account created!"})


@app.route("/api/user/<int:user_id>", methods=["GET"])
def api_user_get(user_id):
    user = User.query.get_or_404(user_id)
    return jsonify({"user": user.to_dict()})


# ============================================================
# Price Alert Endpoints
# ============================================================

@app.route("/api/alerts", methods=["POST"])
def api_alert_create():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    alert = PriceAlert(
        user_id=user_id,
        search_query=data.get("query", "").strip(),
        target_price=float(data["target_price"]) if data.get("target_price") else None,
        min_discount=int(data["min_discount"]) if data.get("min_discount") else None,
        notify_email=data.get("notify_email", True),
        notify_sms=data.get("notify_sms", False),
    )
    db.session.add(alert)
    db.session.commit()
    return jsonify({"alert": alert.to_dict(), "message": "Alert created!"})


@app.route("/api/alerts/<int:user_id>", methods=["GET"])
def api_alerts_list(user_id):
    alerts = PriceAlert.query.filter_by(user_id=user_id, is_active=True).all()
    return jsonify({"alerts": [a.to_dict() for a in alerts]})


@app.route("/api/alerts/<int:alert_id>/delete", methods=["POST"])
def api_alert_delete(alert_id):
    alert = PriceAlert.query.get_or_404(alert_id)
    alert.is_active = False
    db.session.commit()
    return jsonify({"message": "Alert removed"})


# ============================================================
# Price History Endpoint
# ============================================================

@app.route("/api/price-history/<int:deal_id>", methods=["GET"])
def api_price_history(deal_id):
    history = get_price_history(deal_id)
    deal = Deal.query.get(deal_id)
    return jsonify({
        "deal": deal.to_dict() if deal else None,
        "history": history,
    })


# ============================================================
# Saved Searches
# ============================================================

@app.route("/api/saved-searches", methods=["POST"])
def api_save_search():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    ss = SavedSearch(
        user_id=user_id,
        name=data.get("name", data.get("query", "")[:50]),
        query=data.get("query", ""),
        source=data.get("source", "amazon"),
        filters_json=json.dumps(data.get("filters", {})),
    )
    db.session.add(ss)
    db.session.commit()
    return jsonify({"saved_search": ss.to_dict(), "message": "Search saved!"})


@app.route("/api/saved-searches/<int:user_id>", methods=["GET"])
def api_saved_searches_list(user_id):
    searches = SavedSearch.query.filter_by(user_id=user_id).order_by(SavedSearch.created_at.desc()).all()
    return jsonify({"saved_searches": [s.to_dict() for s in searches]})


@app.route("/api/saved-searches/<int:search_id>/delete", methods=["POST"])
def api_saved_search_delete(search_id):
    ss = SavedSearch.query.get_or_404(search_id)
    db.session.delete(ss)
    db.session.commit()
    return jsonify({"message": "Search removed"})


# ============================================================
# Wishlist
# ============================================================

@app.route("/api/wishlist", methods=["POST"])
def api_wishlist_add():
    data = request.json or {}
    user_id = data.get("user_id")
    if not user_id:
        return jsonify({"error": "User ID required"}), 400
    item = Wishlist(
        user_id=user_id,
        name=data.get("name", "")[:500],
        url=data.get("url", ""),
        target_price=float(data["target_price"]) if data.get("target_price") else None,
        current_price=float(data["current_price"]) if data.get("current_price") else None,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({"item": item.to_dict(), "message": "Added to wishlist!"})


@app.route("/api/wishlist/<int:user_id>", methods=["GET"])
def api_wishlist_list(user_id):
    items = Wishlist.query.filter_by(user_id=user_id).order_by(Wishlist.added_at.desc()).all()
    return jsonify({"wishlist": [i.to_dict() for i in items]})


@app.route("/api/wishlist/<int:item_id>/delete", methods=["POST"])
def api_wishlist_delete(item_id):
    item = Wishlist.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({"message": "Removed from wishlist"})


# ============================================================
# API Config Status
# ============================================================

@app.route("/api/config/status", methods=["GET"])
def api_config_status():
    """Check which API integrations are configured."""
    return jsonify({
        "email": bool(os.environ.get("SMTP_HOST")),
        "sms": bool(os.environ.get("TWILIO_SID")),
        "amazon_api": bool(os.environ.get("AMAZON_ACCESS_KEY")),
        "ebay_api": bool(os.environ.get("EBAY_APP_ID")),
        "walmart_api": bool(os.environ.get("WALMART_API_KEY")),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
