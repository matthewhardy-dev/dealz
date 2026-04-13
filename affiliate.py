"""Affiliate link tagging for Deal Finder Pro.
Automatically adds affiliate tags to outbound product URLs.

Environment variables:
  AMAZON_ASSOCIATE_TAG  — Amazon Associates tag (e.g., dealfinder-20)
  EBAY_CAMPAIGN_ID      — eBay Partner Network campaign ID
  WALMART_AFFILIATE_ID  — Walmart affiliate ID
  IMPACT_AFFILIATE_ID   — Impact.com affiliate ID (for other stores)
"""
import os
import re
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

AMAZON_TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
EBAY_CAMPAIGN = os.environ.get("EBAY_CAMPAIGN_ID", "")
WALMART_AFF = os.environ.get("WALMART_AFFILIATE_ID", "")


def tag_affiliate_link(url, store=""):
    """Add affiliate parameters to a product URL."""
    if not url or url == "#":
        return url

    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    params = parse_qs(parsed.query)

    # Amazon
    if "amazon.com" in domain and AMAZON_TAG:
        params["tag"] = [AMAZON_TAG]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # eBay
    if "ebay.com" in domain and EBAY_CAMPAIGN:
        params["campid"] = [EBAY_CAMPAIGN]
        params["toolid"] = ["10001"]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    # Walmart
    if "walmart.com" in domain and WALMART_AFF:
        params["affiliates_id"] = [WALMART_AFF]
        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    return url


def tag_deals_list(deals):
    """Tag all deals in a list with affiliate links."""
    for deal in deals:
        if deal.get("url"):
            deal["url"] = tag_affiliate_link(deal["url"], deal.get("store", ""))
    return deals
