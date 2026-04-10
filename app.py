import os
import json
from flask import Flask, render_template, request, jsonify
from smartscraper import SmartScraper
from smartscraper.deals import (scrape_search_deals, scrape_goldbox_deals, get_deal_pages,
                               scrape_ebay_deals, scrape_ebay_search_deals, EBAY_DEAL_PAGES,
                               scrape_multi_store_deals)

app = Flask(__name__)
scraper = SmartScraper()
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
TEMP_RULES = os.path.join(MODELS_DIR, "_session.json")
os.makedirs(MODELS_DIR, exist_ok=True)

# Restore rules from last session (survives debug reloader)
if os.path.exists(TEMP_RULES):
    try:
        scraper.load(TEMP_RULES)
    except Exception:
        pass


def _persist():
    """Auto-save current rules so they survive reloads."""
    try:
        scraper.save(os.path.join(MODELS_DIR, "_session"))
    except Exception:
        pass


@app.route("/")
def index():
    models = [f.replace(".json", "") for f in os.listdir(MODELS_DIR)
              if f.endswith(".json") and f != "_session.json"]
    return render_template("index.html", models=models)


@app.route("/api/build", methods=["POST"])
def api_build():
    data = request.json
    url = data.get("url", "").strip()
    wanted = [w.strip() for w in data.get("wanted_list", []) if w.strip()]
    if not url or not wanted:
        return jsonify({"error": "URL and at least one sample value required"}), 400
    try:
        use_js = data.get("use_js", False)
        results = scraper.build(url=url, wanted_list=wanted, use_js=use_js)
        rules = scraper.get_rules()
        _persist()
        if not rules:
            return jsonify({
                "error": f"No matching rules found. The sample data wasn't found on the page. Try copying exact text from the page.",
                "results": [],
                "rules": {},
                "rule_count": 0
            }), 200
        return jsonify({"results": results, "rules": rules, "rule_count": len(rules)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = request.json
    url = data.get("url", "").strip()
    mode = data.get("mode", "similar")
    if not url:
        return jsonify({"error": "URL required"}), 400
    if not scraper.get_rules():
        return jsonify({"error": "No rules learned yet. Use 'Build Rules' first with a URL and sample data."}), 400
    try:
        use_js = data.get("use_js", False)
        if mode == "exact":
            results = scraper.get_result_exact(url=url, use_js=use_js)
        else:
            results = scraper.get_result_similar(url=url, use_js=use_js)
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/save", methods=["POST"])
def api_save():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "Model name required"}), 400
    try:
        path = os.path.join(MODELS_DIR, name)
        scraper.save(path)
        return jsonify({"message": f"Model '{name}' saved"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/load", methods=["POST"])
def api_load():
    name = request.json.get("name", "").strip()
    if not name:
        return jsonify({"error": "Model name required"}), 400
    try:
        path = os.path.join(MODELS_DIR, name)
        scraper.load(path)
        _persist()
        return jsonify({"message": f"Model '{name}' loaded", "rules": scraper.get_rules()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/rules", methods=["GET"])
def api_rules():
    return jsonify({"rules": scraper.get_rules()})


@app.route("/api/rules/<rule_id>", methods=["DELETE"])
def api_delete_rule(rule_id):
    scraper.remove_rule(rule_id)
    _persist()
    return jsonify({"message": "Rule removed", "rules": scraper.get_rules()})


@app.route("/api/deals/search", methods=["POST"])
def api_deals_search():
    data = request.json
    query = data.get("query", "deals").strip()
    max_results = data.get("max_results", 30)
    try:
        deals = scrape_search_deals(query, max_results)
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/goldbox", methods=["POST"])
def api_deals_goldbox():
    try:
        deals = scrape_goldbox_deals()
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
    try:
        deals = scrape_ebay_deals(url, max_results=data.get("max_results", 30))
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/ebay/search", methods=["POST"])
def api_ebay_search():
    data = request.json
    query = data.get("query", "deals").strip()
    try:
        deals = scrape_ebay_search_deals(query, max_results=data.get("max_results", 30))
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/deals/google", methods=["POST"])
def api_google_deals():
    data = request.json
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Search term required"}), 400
    try:
        deals = scrape_multi_store_deals(query, max_results=data.get("max_results", 30))
        return jsonify({"deals": deals, "count": len(deals)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
