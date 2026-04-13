"""Official API integrations for Deal Finder Pro.
These provide reliable, structured data when API keys are configured.
Falls back to scraping when keys aren't available.

Environment variables needed:
  Amazon:  AMAZON_ACCESS_KEY, AMAZON_SECRET_KEY, AMAZON_ASSOCIATE_TAG
  eBay:    EBAY_APP_ID
  Walmart: WALMART_API_KEY
"""
import os
import re
import json
import hmac
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus
import requests

# ============================================================
# Amazon Product Advertising API (PA-API 5.0)
# ============================================================

AMAZON_ACCESS_KEY = os.environ.get("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET_KEY = os.environ.get("AMAZON_SECRET_KEY", "")
AMAZON_ASSOCIATE_TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
AMAZON_HOST = "webservices.amazon.com"
AMAZON_REGION = "us-east-1"


def amazon_api_available():
    return bool(AMAZON_ACCESS_KEY and AMAZON_SECRET_KEY and AMAZON_ASSOCIATE_TAG)


def _amazon_sign(payload_json, operation):
    """Sign an Amazon PA-API request using AWS Signature V4."""
    service = "ProductAdvertisingAPI"
    endpoint = f"https://{AMAZON_HOST}/paapi5/{operation.lower()}"
    t = datetime.now(timezone.utc)
    date_stamp = t.strftime("%Y%m%d")
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")

    headers = {
        "content-type": "application/json; charset=utf-8",
        "host": AMAZON_HOST,
        "x-amz-date": amz_date,
        "x-amz-target": f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}",
        "content-encoding": "amz-1.0",
    }
    signed_headers = ";".join(sorted(headers.keys()))
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(headers.items()))

    payload_hash = hashlib.sha256(payload_json.encode()).hexdigest()
    canonical_request = f"POST\n/paapi5/{operation.lower()}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    credential_scope = f"{date_stamp}/{AMAZON_REGION}/{service}/aws4_request"
    string_to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

    def _sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    k_date = _sign(f"AWS4{AMAZON_SECRET_KEY}".encode(), date_stamp)
    k_region = _sign(k_date, AMAZON_REGION)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={AMAZON_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return endpoint, headers


def amazon_search(query, max_results=30):
    """Search Amazon via PA-API. Returns list of deal dicts."""
    if not amazon_api_available():
        return None  # Caller should fall back to scraping

    payload = {
        "Keywords": query,
        "Resources": [
            "ItemInfo.Title",
            "Offers.Listings.Price",
            "Offers.Listings.SavingBasis",
            "Offers.Listings.MerchantInfo",
            "Offers.Listings.Condition",
            "Offers.Listings.Promotions",
            "BrowseNodeInfo.BrowseNodes",
            "CustomerReviews.StarRating",
            "CustomerReviews.Count",
        ],
        "ItemCount": min(max_results, 10),
        "PartnerTag": AMAZON_ASSOCIATE_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
    }
    payload_json = json.dumps(payload)

    try:
        endpoint, headers = _amazon_sign(payload_json, "SearchItems")
        resp = requests.post(endpoint, headers=headers, data=payload_json, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Amazon API] Error: {e}")
        return None

    deals = []
    for item in data.get("SearchResult", {}).get("Items", []):
        deal = _parse_amazon_item(item)
        if deal:
            deals.append(deal)
    return deals


def _parse_amazon_item(item):
    """Parse a PA-API item into a deal dict."""
    deal = {}
    deal["name"] = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
    if not deal["name"]:
        return None

    asin = item.get("ASIN", "")
    # Affiliate link
    tag = AMAZON_ASSOCIATE_TAG
    deal["url"] = f"https://www.amazon.com/dp/{asin}?tag={tag}" if asin else ""

    listings = item.get("Offers", {}).get("Listings", [])
    if listings:
        listing = listings[0]
        price_info = listing.get("Price", {})
        deal["price"] = price_info.get("Amount")
        deal["price_str"] = price_info.get("DisplayAmount", "")

        saving_basis = listing.get("SavingBasis", {})
        if saving_basis:
            deal["original_price"] = saving_basis.get("Amount")
            deal["original_price_str"] = saving_basis.get("DisplayAmount", "")
            if deal.get("price") and deal.get("original_price") and deal["original_price"] > deal["price"]:
                deal["discount_pct"] = round((1 - deal["price"] / deal["original_price"]) * 100)

        condition = listing.get("Condition", {}).get("Value", "New")
        deal["condition"] = condition

        promos = listing.get("Promotions", [])
        if promos:
            deal["coupon"] = promos[0].get("DiscountPercent", "")

    reviews = item.get("CustomerReviews", {})
    if reviews.get("StarRating", {}).get("Value"):
        deal["rating"] = reviews["StarRating"]["Value"]
    if reviews.get("Count"):
        deal["reviews"] = reviews["Count"]

    deal["store"] = "Amazon"
    return deal


# ============================================================
# eBay Browse API
# ============================================================

EBAY_APP_ID = os.environ.get("EBAY_APP_ID", "")
EBAY_API_URL = "https://api.ebay.com/buy/browse/v1"


def ebay_api_available():
    return bool(EBAY_APP_ID)


def _get_ebay_token():
    """Get eBay OAuth token using client credentials."""
    try:
        resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            auth=(EBAY_APP_ID, os.environ.get("EBAY_SECRET", "")),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
    except Exception as e:
        print(f"[eBay API] Token error: {e}")
        return None


def ebay_search(query, max_results=30):
    """Search eBay via Browse API."""
    if not ebay_api_available():
        return None

    token = _get_ebay_token()
    if not token:
        return None

    try:
        resp = requests.get(
            f"{EBAY_API_URL}/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            },
            params={
                "q": query,
                "limit": min(max_results, 50),
                "filter": "buyingOptions:{FIXED_PRICE}",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[eBay API] Error: {e}")
        return None

    deals = []
    for item in data.get("itemSummaries", []):
        deal = {}
        deal["name"] = item.get("title", "")
        if not deal["name"]:
            continue

        deal["url"] = item.get("itemWebUrl", "")
        price = item.get("price", {})
        deal["price"] = float(price.get("value", 0))
        deal["price_str"] = f"${deal['price']:,.2f}" if deal["price"] else ""

        orig = item.get("marketingPrice", {})
        if orig.get("originalPrice", {}).get("value"):
            deal["original_price"] = float(orig["originalPrice"]["value"])
            deal["original_price_str"] = f"${deal['original_price']:,.2f}"
            if deal["price"] and deal["original_price"] > deal["price"]:
                deal["discount_pct"] = round((1 - deal["price"] / deal["original_price"]) * 100)
            disc = orig.get("discountPercentage")
            if disc:
                deal["discount_pct"] = int(disc)

        cond = item.get("condition", "New")
        deal["condition"] = cond if cond != "NEW" else "New"
        deal["store"] = "eBay"

        if item.get("shippingOptions"):
            ship = item["shippingOptions"][0]
            if ship.get("shippingCost", {}).get("value") == "0.00":
                deal["badge"] = "Free Shipping"

        if deal.get("price"):
            deals.append(deal)

    return deals


# ============================================================
# Walmart Affiliate API
# ============================================================

WALMART_API_KEY = os.environ.get("WALMART_API_KEY", "")
WALMART_AFFILIATE_ID = os.environ.get("WALMART_AFFILIATE_ID", "")


def walmart_api_available():
    return bool(WALMART_API_KEY)


def walmart_search(query, max_results=25):
    """Search Walmart via Affiliate API."""
    if not walmart_api_available():
        return None

    try:
        resp = requests.get(
            "https://developer.api.walmart.com/api-proxy/service/affil/product/v2/search",
            headers={"WM_SEC.KEY_VERSION": "1", "WM_CONSUMER.ID": WALMART_API_KEY},
            params={"query": query, "numItems": min(max_results, 25), "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[Walmart API] Error: {e}")
        return None

    deals = []
    for item in data.get("items", []):
        deal = {}
        deal["name"] = item.get("name", "")
        if not deal["name"]:
            continue

        url = item.get("productUrl", "")
        if WALMART_AFFILIATE_ID:
            url += f"&affiliates_id={WALMART_AFFILIATE_ID}" if "?" in url else f"?affiliates_id={WALMART_AFFILIATE_ID}"
        deal["url"] = url

        deal["price"] = item.get("salePrice") or item.get("msrp")
        deal["price_str"] = f"${deal['price']:,.2f}" if deal.get("price") else ""

        msrp = item.get("msrp")
        sale = item.get("salePrice")
        if msrp and sale and msrp > sale:
            deal["original_price"] = msrp
            deal["original_price_str"] = f"${msrp:,.2f}"
            deal["discount_pct"] = round((1 - sale / msrp) * 100)

        deal["rating"] = item.get("customerRating")
        if deal["rating"]:
            deal["rating"] = float(deal["rating"])
        deal["reviews"] = item.get("numReviews")

        if item.get("freeShippingOver35Dollars") or item.get("freeShipping"):
            deal["badge"] = "Free Shipping"

        deal["condition"] = "New"
        deal["store"] = "Walmart"

        if deal.get("price"):
            deals.append(deal)

    return deals


# ============================================================
# Unified search — tries APIs first, falls back to scraping
# ============================================================

def search_with_api_fallback(source, query, max_results=30, scrape_func=None, **kwargs):
    """Try official API first, fall back to scraping if unavailable."""
    api_funcs = {
        "amazon": (amazon_api_available, amazon_search),
        "ebay": (ebay_api_available, ebay_search),
        "walmart": (walmart_api_available, walmart_search),
    }

    if source in api_funcs:
        available_fn, search_fn = api_funcs[source]
        if available_fn():
            result = search_fn(query, max_results)
            if result is not None:
                return result, "api"

    # Fall back to scraping
    if scrape_func:
        return scrape_func(query, max_results, **kwargs), "scrape"
    return [], "none"
