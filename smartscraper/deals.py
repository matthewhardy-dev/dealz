"""Deal Finder - Scrapes Amazon & eBay deals with prices, discounts, and direct links."""
import os
import re
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup
import requests

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

AMAZON_DEAL_PAGES = {
    "todays_deals": "https://www.amazon.com/gp/goldbox",
    "lightning": "https://www.amazon.com/gp/goldbox?deals-widget=%2Fwidgets%2Fdeals-widget%2Fv2%2Fcreate-widget&gb_f_deals1=dealStates:AVAILABLE%252CWAITLISTFULL%252CUPCOMING,dealTypes:LIGHTNING_DEAL",
    "best_sellers": "https://www.amazon.com/gp/bestsellers",
    "movers_shakers": "https://www.amazon.com/gp/movers-and-shakers",
    "new_releases": "https://www.amazon.com/gp/new-releases",
    "coupons": "https://www.amazon.com/Amazon-Coupons/b?node=2231352011",
    "outlet": "https://www.amazon.com/outlet",
    "warehouse": "https://www.amazon.com/warehouse",
}

EBAY_DEAL_PAGES = {
    "daily_deals": "https://www.ebay.com/deals",
    "tech_deals": "https://www.ebay.com/deals/tech",
    "fashion_deals": "https://www.ebay.com/deals/fashion",
    "home_deals": "https://www.ebay.com/deals/home-and-garden",
    "global_deals": "https://www.ebay.com/globaldeals",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Per-unit price patterns to ignore
PER_UNIT_RE = re.compile(r"\$[\d.]+\s*/\s*(?:sq|square|linear|cubic)?\s*(?:ft|foot|feet|yard|meter|inch|oz|ounce|lb|pound|count|ct|ea|each|unit|piece|pc|roll|sheet|tile|pack|gal|gallon|liter|ml|fl|gram|kg)", re.I)


def _fetch_simple(url):
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
    for attempt in range(3):
        try:
            res = requests.get(url, headers=headers, timeout=25)
            res.raise_for_status()
            return res.text
        except Exception:
            if attempt == 2:
                raise
            import time
            time.sleep(2)
    return res.text


def _fetch_page(url, scroll_count=4, wait_ms=2000):
    if not HAS_PLAYWRIGHT:
        return _fetch_simple(url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(wait_ms)
            for i in range(scroll_count):
                page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {(i+1)/scroll_count})")
                page.wait_for_timeout(1500)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(500)
            html = page.content()
            browser.close()
        return html
    except Exception:
        return _fetch_simple(url)


def _parse_price(text):
    match = re.search(r"\$[\d,]+\.?\d*", text)
    if match:
        return float(match.group().replace("$", "").replace(",", ""))
    return None


def _parse_percent(text):
    match = re.search(r"(\d+)%", text)
    if match:
        return int(match.group(1))
    return None


def _is_per_unit_price(text, price_pos):
    """Check if a price at a given position is a per-unit price."""
    after = text[price_pos:price_pos + 80]
    return bool(PER_UNIT_RE.search(after))


def _clean_discount(pct):
    """Cap discount at 99% and reject nonsensical values."""
    if pct is None:
        return None
    if pct > 99 or pct < 1:
        return None
    return pct


def _validate_prices(deal):
    """Sanity-check price vs original price. Remove bogus discounts."""
    price = deal.get("price")
    orig = deal.get("original_price")
    if price and orig:
        # If original is more than 15x the sale price, it's almost certainly wrong
        if orig > price * 15:
            deal.pop("original_price", None)
            deal.pop("original_price_str", None)
            deal.pop("discount_pct", None)
        # If original is less than or equal to sale price, no discount
        elif orig <= price:
            deal.pop("original_price", None)
            deal.pop("original_price_str", None)
            deal.pop("discount_pct", None)
    return deal


def _detect_condition(text):
    """Detect item condition from text."""
    t = text.lower()
    if "refurbished" in t or "renewed" in t:
        return "Refurbished"
    if "used" in t or "pre-owned" in t or "pre owned" in t:
        return "Used"
    if "open box" in t or "open-box" in t:
        return "Open Box"
    if "new" in t:
        return "New"
    return None


def _apply_filters(deals, filters):
    """Apply advanced search filters to deal list."""
    if not filters:
        return deals
    result = []
    for d in deals:
        # Min/max price
        if filters.get("min_price") and d.get("price", 0) < filters["min_price"]:
            continue
        if filters.get("max_price") and d.get("price", 99999) > filters["max_price"]:
            continue
        # Min discount
        if filters.get("min_discount") and (d.get("discount_pct") or 0) < filters["min_discount"]:
            continue
        # Condition
        if filters.get("condition") and filters["condition"] != "any":
            if d.get("condition", "").lower() != filters["condition"].lower():
                continue
        # Must contain words
        if filters.get("must_contain"):
            name_lower = d.get("name", "").lower()
            if not all(w.lower() in name_lower for w in filters["must_contain"]):
                continue
        # Exclude words
        if filters.get("exclude"):
            name_lower = d.get("name", "").lower()
            if any(w.lower() in name_lower for w in filters["exclude"]):
                continue
        result.append(d)
    return result


def _sort_deals(deals, sort_by="cheapest"):
    """Sort deals by different criteria."""
    if sort_by == "cheapest":
        return sorted(deals, key=lambda d: d.get("price", 99999))
    elif sort_by == "expensive":
        return sorted(deals, key=lambda d: d.get("price", 0), reverse=True)
    elif sort_by == "discount":
        return sorted(deals, key=lambda d: d.get("discount_pct", 0), reverse=True)
    elif sort_by == "rating":
        return sorted(deals, key=lambda d: d.get("rating", 0), reverse=True)
    return deals


# ============================================================
# Amazon
# ============================================================

def scrape_search_deals(query="deals", max_results=30, page=1, filters=None, sort_by="cheapest"):
    """Scrape a single page of Amazon search results."""
    url = f"https://www.amazon.com/s?k={quote_plus(query)}&page={page}"
    html = _fetch_page(url)
    soup = BeautifulSoup(html, "lxml")
    deals = []

    cards = soup.find_all("div", attrs={"data-component-type": "s-search-result"})
    if not cards:
        cards = soup.find_all("div", attrs={"data-asin": True})
        cards = [c for c in cards if c.get("data-asin")]

    for card in cards:
        deal = {}
        asin = card.get("data-asin", "")
        if not asin:
            continue

        # Link
        link_el = card.find("a", class_=re.compile("a-link-normal.*s-line-clamp"))
        if not link_el:
            link_el = card.find("a", href=re.compile(r"/dp/|/gp/"))
        if link_el:
            deal["url"] = urljoin("https://www.amazon.com", link_el.get("href", ""))
        else:
            deal["url"] = f"https://www.amazon.com/dp/{asin}"

        # Name
        img = card.find("img", alt=True)
        if img and len(img["alt"]) > 10:
            deal["name"] = img["alt"]
        else:
            h2 = card.find("h2")
            if h2:
                deal["name"] = h2.get_text().strip()
        if "name" not in deal:
            continue

        name = deal["name"]
        for prefix in ["Sponsored Ad - ", "Sponsored "]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        # Strip certification descriptions that bleed into names
        name = re.sub(r"Global Recycled Standard.*$", "", name, flags=re.I|re.S).strip()
        name = re.sub(r"Climate Pledge Friendly.*$", "", name, flags=re.I|re.S).strip()
        name = re.sub(r"(?:GRS|OEKO-TEX|FSC) certified.*$", "", name, flags=re.I|re.S).strip()
        deal["name"] = name

        # Price
        price_whole = card.find("span", class_="a-price-whole")
        price_frac = card.find("span", class_="a-price-fraction")
        if price_whole:
            whole = price_whole.get_text().strip().rstrip(".")
            frac = price_frac.get_text().strip() if price_frac else "00"
            deal["price"] = _parse_price(f"${whole}.{frac}")
            deal["price_str"] = f"${whole}.{frac}"

        # Original price
        original_prices = card.find_all("span", class_="a-price")
        if len(original_prices) >= 2:
            orig = original_prices[1].find("span", class_="a-offscreen")
            if orig:
                deal["original_price"] = _parse_price(orig.get_text())
                deal["original_price_str"] = orig.get_text().strip()

        # Discount
        discount_el = card.find(string=re.compile(r"\d+%\s*off", re.I))
        if discount_el:
            deal["discount_pct"] = _clean_discount(_parse_percent(discount_el))
        elif deal.get("price") and deal.get("original_price") and deal["original_price"] > 0:
            deal["discount_pct"] = _clean_discount(round((1 - deal["price"] / deal["original_price"]) * 100))

        # Rating
        rating_el = card.find("span", class_="a-icon-alt")
        if rating_el:
            rmatch = re.search(r"([\d.]+) out of", rating_el.get_text())
            if rmatch:
                deal["rating"] = float(rmatch.group(1))

        # Reviews
        review_el = card.find("span", class_=re.compile("a-size-base.*s-underline-text"))
        if review_el:
            rtext = review_el.get_text().strip().replace(",", "")
            if rtext.isdigit():
                deal["reviews"] = int(rtext)

        # Badge — grab short labels only, skip long certification descriptions
        badge = card.find(string=re.compile(r"Limited time deal|Lightning Deal|Best Seller|Climate Pledge Friendly", re.I))
        if badge:
            badge_text = badge.strip()
            # Only keep short badge text (skip full certification paragraphs)
            if len(badge_text) <= 60:
                deal["badge"] = badge_text
            else:
                # Extract just the label from long text
                for label in ["Climate Pledge Friendly", "Best Seller", "Lightning Deal", "Limited time deal"]:
                    if label.lower() in badge_text.lower():
                        deal["badge"] = label
                        break

        # Condition
        card_text = card.get_text().lower()
        deal["condition"] = _detect_condition(card_text) or "New"

        # Coupon
        coupon = card.find(string=re.compile(r"coupon|save \$\d+", re.I))
        if coupon:
            cmatch = re.search(r"\$(\d+)", coupon)
            if cmatch:
                deal["coupon"] = f"${cmatch.group(1)} coupon"
            else:
                deal["coupon"] = coupon.strip()[:40]

        _validate_prices(deal)
        deals.append(deal)

    deals = _apply_filters(deals, filters)
    deals = _sort_deals(deals, sort_by)
    return deals[:max_results]


def scrape_goldbox_deals(filters=None, sort_by="cheapest"):
    """Scrape Amazon Today's Deals page."""
    html = _fetch_page(AMAZON_DEAL_PAGES["todays_deals"], scroll_count=5, wait_ms=3000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen_names = set()

    offscreens = soup.find_all("span", class_="a-offscreen")
    i = 0
    while i < len(offscreens):
        text = offscreens[i].get_text().strip()
        if not text.startswith("Deal Price:"):
            i += 1
            continue

        deal = {}
        deal_price = _parse_price(text)
        if deal_price:
            deal["price"] = deal_price
            deal["price_str"] = f"${deal_price:,.2f}"

        if i + 1 < len(offscreens):
            next_text = offscreens[i + 1].get_text().strip()
            if next_text.startswith("List:"):
                list_price = _parse_price(next_text)
                if list_price:
                    deal["original_price"] = list_price
                    deal["original_price_str"] = f"${list_price:,.2f}"
                    if deal.get("price") and list_price > deal["price"]:
                        deal["discount_pct"] = _clean_discount(round((1 - deal["price"] / list_price) * 100))

        for j in range(i + 1, min(i + 4, len(offscreens))):
            candidate = offscreens[j].get_text().strip()
            if not candidate.startswith(("Deal Price:", "List:", "Shop ", "See ")) and len(candidate) > 15:
                deal["name"] = candidate
                break

        if "name" not in deal:
            i += 1
            continue
        if deal["name"] in seen_names:
            i += 1
            continue
        seen_names.add(deal["name"])

        container = offscreens[i].parent
        for _ in range(10):
            if container is None:
                break
            link = container.find("a", href=re.compile(r"/dp/"))
            if link:
                deal["url"] = urljoin("https://www.amazon.com", link["href"])
                break
            container = container.parent

        if "url" not in deal:
            deal["url"] = "https://www.amazon.com/gp/goldbox"

        if container:
            badge_match = container.find(string=re.compile(r"Limited time deal|Lightning Deal|Deal of the Day", re.I))
            if badge_match:
                deal["badge"] = badge_match.strip()

        deal["condition"] = "New"
        _validate_prices(deal)
        deals.append(deal)
        i += 1

    deals = _apply_filters(deals, filters)
    deals = _sort_deals(deals, sort_by)
    return deals


def scrape_amazon_comprehensive(query="deals", max_results=200, filters=None, sort_by="cheapest"):
    """
    Comprehensive Amazon search: scrapes multiple search result pages
    AND all Amazon deal pages in parallel to maximize results.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_deals = []
    seen_asins = set()
    seen_names = set()

    # Calculate how many search pages we need (Amazon shows ~60 per page)
    search_pages = max(1, min(max_results // 50, 7))  # Up to 7 pages

    tasks = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        # Multiple search result pages
        for pg in range(1, search_pages + 1):
            tasks[pool.submit(scrape_search_deals, query, 60, pg)] = f"Search page {pg}"

        # Also search Amazon deal pages for the query
        tasks[pool.submit(scrape_goldbox_deals)] = "Goldbox"

        for future in as_completed(tasks):
            try:
                deals = future.result()
                for d in deals:
                    # Deduplicate by URL (contains ASIN) or name
                    url = d.get("url", "")
                    asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
                    if asin_match:
                        asin = asin_match.group(1)
                        if asin in seen_asins:
                            continue
                        seen_asins.add(asin)
                    else:
                        name_key = re.sub(r"[^a-z0-9]", "", d.get("name", "").lower())[:50]
                        if name_key in seen_names:
                            continue
                        seen_names.add(name_key)
                    all_deals.append(d)
            except Exception:
                pass

    # If query provided, boost relevance — items matching query words come first
    if query:
        query_words = query.lower().split()
        for d in all_deals:
            name_lower = d.get("name", "").lower()
            d["_relevance"] = sum(1 for w in query_words if w in name_lower)

    all_deals = _apply_filters(all_deals, filters)
    all_deals = _sort_deals(all_deals, sort_by)
    return all_deals[:max_results]


def scrape_ebay_comprehensive(query="deals", max_results=200, filters=None, sort_by="cheapest"):
    """
    Comprehensive eBay search: scrapes multiple search result pages
    AND all eBay deal pages in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_deals = []
    seen_urls = set()
    seen_names = set()

    search_pages = max(1, min(max_results // 40, 5))

    tasks = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        # Multiple search pages
        for pg in range(1, search_pages + 1):
            page_url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&LH_BIN=1&_pgn={pg}"
            tasks[pool.submit(_scrape_ebay_page, page_url, 60)] = f"Search page {pg}"

        # All eBay deal pages
        for page_name, page_url in EBAY_DEAL_PAGES.items():
            tasks[pool.submit(scrape_ebay_deals, page_url, 40)] = f"eBay {page_name}"

        for future in as_completed(tasks):
            try:
                deals = future.result()
                for d in deals:
                    url_key = d.get("url", "").split("?")[0]
                    if url_key in seen_urls:
                        continue
                    seen_urls.add(url_key)
                    name_key = re.sub(r"[^a-z0-9]", "", d.get("name", "").lower())[:50]
                    if name_key in seen_names:
                        continue
                    seen_names.add(name_key)
                    all_deals.append(d)
            except Exception:
                pass

    all_deals = _apply_filters(all_deals, filters)
    all_deals = _sort_deals(all_deals, sort_by)
    return all_deals[:max_results]


def _scrape_ebay_page(url, max_results=60):
    """Scrape a single eBay search results page (used by comprehensive search)."""
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    links = [l for l in soup.find_all("a", href=re.compile(r"ebay\.com/itm/")) if len(l.get_text().strip()) > 20]

    for link in links:
        name = link.get_text().strip()
        for suffix in ["Opens in a new window or tab", "Opens in a new window"]:
            name = name.replace(suffix, "").strip()
        if not name or name in seen or name == "Shop on eBay":
            continue
        seen.add(name)

        deal = {"name": name, "url": link.get("href", "").split("?")[0]}

        card = link.parent
        for _ in range(5):
            if card is None:
                break
            classes = card.get("class", [])
            class_str = " ".join(classes)
            if ("su-card-container" in classes and "__" not in class_str) or "s-card" in classes:
                break
            card = card.parent

        if card:
            text = card.get_text(separator=" | ")
            item_prices = re.findall(r"(?<!\+)\$([\d,]+\.\d{2})", text)
            item_prices = [float(p.replace(",", "")) for p in item_prices]
            item_prices = [p for p in item_prices if 0.5 < p < 50000]

            if item_prices:
                deal["price"] = item_prices[0]
                deal["price_str"] = f"${item_prices[0]:,.2f}"
                if len(item_prices) >= 2 and item_prices[1] > item_prices[0]:
                    deal["original_price"] = item_prices[1]
                    deal["original_price_str"] = f"${item_prices[1]:,.2f}"
                    deal["discount_pct"] = _clean_discount(round((1 - item_prices[0] / item_prices[1]) * 100))

            pct = re.search(r"(\d+)%\s*off", text, re.I)
            if pct:
                deal["discount_pct"] = _clean_discount(int(pct.group(1)))
            if "free delivery" in text.lower() or "free shipping" in text.lower():
                deal["badge"] = "Free Shipping"

            deal["condition"] = _detect_condition(text) or "New"

        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# eBay
# ============================================================

def scrape_ebay_deals(url="https://www.ebay.com/deals", max_results=30, filters=None, sort_by="cheapest"):
    html = _fetch_page(url, scroll_count=4, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    links = [l for l in soup.find_all("a", href=re.compile(r"ebay\.com/itm/")) if len(l.get_text().strip()) > 10]

    for link in links:
        name = link.get_text().strip()
        for suffix in ["Opens in a new window or tab", "Opens in a new window"]:
            name = name.replace(suffix, "").strip()
        if not name or name in seen or len(name) < 10:
            continue
        seen.add(name)

        deal = {"name": name, "url": link.get("href", "").split("?")[0]}

        card = link.parent
        for _ in range(5):
            if card is None:
                break
            classes = card.get("class", [])
            class_str = " ".join(classes)
            if ("su-card-container" in classes and "__" not in class_str) or "s-card" in classes:
                break
            card = card.parent

        if card:
            text = card.get_text(separator=" | ")
            # Skip per-unit prices
            item_prices = re.findall(r"(?<!\+)\$([\d,]+\.\d{2})", text)
            item_prices = [float(p.replace(",", "")) for p in item_prices]
            item_prices = [p for p in item_prices if 0.5 < p < 50000]

            if item_prices:
                deal["price"] = item_prices[0]
                deal["price_str"] = f"${item_prices[0]:,.2f}"
                if len(item_prices) >= 2 and item_prices[1] > item_prices[0]:
                    deal["original_price"] = item_prices[1]
                    deal["original_price_str"] = f"${item_prices[1]:,.2f}"
                    deal["discount_pct"] = _clean_discount(round((1 - item_prices[0] / item_prices[1]) * 100))

            pct = re.search(r"(\d+)%\s*off", text, re.I)
            if pct:
                deal["discount_pct"] = _clean_discount(int(pct.group(1)))
            if "free delivery" in text.lower() or "free shipping" in text.lower():
                deal["badge"] = "Free Shipping"

            deal["condition"] = _detect_condition(text) or "New"

        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals = _apply_filters(deals, filters)
    deals = _sort_deals(deals, sort_by)
    return deals


def scrape_ebay_search_deals(query="deals", max_results=30, filters=None, sort_by="cheapest"):
    url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&LH_BIN=1"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    links = [l for l in soup.find_all("a", href=re.compile(r"ebay\.com/itm/")) if len(l.get_text().strip()) > 20]

    for link in links:
        name = link.get_text().strip()
        for suffix in ["Opens in a new window or tab", "Opens in a new window"]:
            name = name.replace(suffix, "").strip()
        if not name or name in seen or name == "Shop on eBay":
            continue
        seen.add(name)

        deal = {"name": name, "url": link.get("href", "").split("?")[0]}

        card = link.parent
        for _ in range(5):
            if card is None:
                break
            classes = card.get("class", [])
            class_str = " ".join(classes)
            if ("su-card-container" in classes and "__" not in class_str) or "s-card" in classes:
                break
            card = card.parent

        if card:
            text = card.get_text(separator=" | ")
            item_prices = re.findall(r"(?<!\+)\$([\d,]+\.\d{2})", text)
            item_prices = [float(p.replace(",", "")) for p in item_prices]
            item_prices = [p for p in item_prices if 0.5 < p < 50000]

            if item_prices:
                deal["price"] = item_prices[0]
                deal["price_str"] = f"${item_prices[0]:,.2f}"
                if len(item_prices) >= 2 and item_prices[1] > item_prices[0]:
                    deal["original_price"] = item_prices[1]
                    deal["original_price_str"] = f"${item_prices[1]:,.2f}"
                    deal["discount_pct"] = _clean_discount(round((1 - item_prices[0] / item_prices[1]) * 100))

            pct = re.search(r"(\d+)%\s*off", text, re.I)
            if pct:
                deal["discount_pct"] = _clean_discount(int(pct.group(1)))
            if "free delivery" in text.lower() or "free shipping" in text.lower():
                deal["badge"] = "Free Shipping"

            deal["condition"] = _detect_condition(text) or "New"

        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals = _apply_filters(deals, filters)
    deals = _sort_deals(deals, sort_by)
    return deals


# ============================================================
# Multi-Store (Slickdeals)
# ============================================================

def scrape_multi_store_deals(query="deals", max_results=30, filters=None, sort_by="cheapest"):
    url = f"https://slickdeals.net/newsearch.php?q={quote_plus(query)}&searcharea=deals&r=1"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen_urls = set()

    cards = soup.find_all("li", class_=re.compile(r"searchPageGrid__feedItem|feedItem"))
    if not cards:
        cards = soup.find_all("div", class_=re.compile(r"dealCard"))

    for card in cards:
        card_text_full = card.get_text(separator=" ").lower()

        # Skip expired deals
        if "expired" in card_text_full or "dead deal" in card_text_full:
            continue

        links = card.find_all("a", href=re.compile(r"/f/\d+"))
        title_link = None
        for l in links:
            href = l.get("href", "")
            text = l.get_text().strip()
            if "#post" in href or text.startswith("$") or len(text) < 15:
                continue
            if not any(c.isalpha() for c in text[:5]):
                continue
            title_link = l
            break

        if not title_link:
            continue

        name = title_link.get_text().strip()
        href = title_link.get("href", "")
        base_url = href.split("?")[0].split("#")[0]
        if base_url in seen_urls:
            continue
        seen_urls.add(base_url)

        name = re.sub(r"^[\$\d,.]+[:\s]*", "", name).strip()
        name = re.sub(r"^[\*|:]+\s*", "", name).strip()
        name = re.sub(r"\s*\+?\s*(?:Free Shipping|Free S/H|\+FS).*$", "", name, flags=re.I).strip()
        name = re.sub(r"\s*(?:w/\s*Prime|or on \$?\d+).*$", "", name, flags=re.I).strip()
        if not name or len(name) < 10:
            continue

        full_url = "https://slickdeals.net" + href if href.startswith("/") else href
        deal = {"name": name, "url": full_url}

        card_text = card.get_text(separator="\n")

        # Detect deal age from timestamps like "12h ago", "3d ago", "2mo ago"
        age_match = re.search(r"(\d+)\s*(h|d|mo|m|w)\s*ago", card_text_full)
        if age_match:
            num = int(age_match.group(1))
            unit = age_match.group(2)
            if unit == "h":
                deal["deal_age"] = f"{num}h ago"
            elif unit == "d":
                deal["deal_age"] = f"{num}d ago"
                if num > 30:
                    continue  # Skip deals older than 30 days
            elif unit in ("mo", "m"):
                deal["deal_age"] = f"{num}mo ago"
                if num > 2:
                    continue  # Skip deals older than 2 months
            elif unit == "w":
                deal["deal_age"] = f"{num}w ago"
                if num > 4:
                    continue  # Skip deals older than 4 weeks

        all_prices = re.findall(r"(?<!\+)\$([\d,]+\.?\d*)", card_text)
        prices = []
        for p in all_prices:
            try:
                val = float(p.replace(",", ""))
                if 0.5 < val < 50000:
                    prices.append(val)
            except ValueError:
                pass
        prices = sorted(set(prices))

        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))
        elif deal.get("price") and deal.get("original_price") and deal["original_price"] > deal["price"]:
            deal["discount_pct"] = _clean_discount(round((1 - deal["price"] / deal["original_price"]) * 100))

        known_stores = ["Amazon", "Walmart", "Best Buy", "Target", "Costco", "Newegg",
                        "B&H", "Home Depot", "Lowe's", "Adorama", "Woot", "Sam's Club",
                        "Micro Center", "Apple", "Dell", "HP", "Lenovo", "eBay"]
        for store in known_stores:
            if store.lower() in card_text.lower():
                deal["store"] = store
                break

        if "free shipping" in card_text.lower() or "+FS" in card_text or "free s/h" in card_text.lower():
            deal["badge"] = "Free Shipping"

        deal["condition"] = _detect_condition(card_text) or "New"

        if deal.get("price") or deal.get("discount_pct"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals = _apply_filters(deals, filters)
    deals = _sort_deals(deals, sort_by)
    return deals


def get_deal_pages():
    return {"amazon": dict(AMAZON_DEAL_PAGES), "ebay": dict(EBAY_DEAL_PAGES)}


# ============================================================
# Google Shopping scraper
# ============================================================

def scrape_google_shopping(query="deals", max_results=60):
    """Scrape Google Shopping results for a query."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&tbm=shop&num=100"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    # Google Shopping cards
    for card in soup.find_all("div", class_=re.compile(r"sh-dgr__content|sh-dlr__list-result")):
        deal = {}
        # Title
        title_el = card.find("h3") or card.find("h4") or card.find("a", class_=re.compile(r"translate-content|shntl"))
        if title_el:
            deal["name"] = title_el.get_text().strip()
        if not deal.get("name") or len(deal["name"]) < 5:
            continue
        if deal["name"] in seen:
            continue
        seen.add(deal["name"])

        # Link
        link = card.find("a", href=True)
        if link:
            href = link.get("href", "")
            if href.startswith("/url?"):
                # Extract actual URL from Google redirect
                url_match = re.search(r"url=([^&]+)", href)
                if url_match:
                    from urllib.parse import unquote
                    deal["url"] = unquote(url_match.group(1))
                else:
                    deal["url"] = "https://www.google.com" + href
            elif href.startswith("http"):
                deal["url"] = href
            else:
                deal["url"] = "https://www.google.com" + href

        # Price
        price_el = card.find(string=re.compile(r"\$[\d,]+\.?\d*"))
        if price_el:
            prices = re.findall(r"\$([\d,]+\.?\d*)", card.get_text())
            prices = [float(p.replace(",", "")) for p in prices if 0.5 < float(p.replace(",", "")) < 50000]
            if prices:
                deal["price"] = min(prices)
                deal["price_str"] = f"${deal['price']:,.2f}"
                if len(prices) >= 2:
                    max_p = max(prices)
                    if max_p > deal["price"] * 1.05:
                        deal["original_price"] = max_p
                        deal["original_price_str"] = f"${max_p:,.2f}"
                        deal["discount_pct"] = _clean_discount(round((1 - deal["price"] / max_p) * 100))

        # Store
        store_el = card.find(string=re.compile(r"Amazon|Walmart|Best Buy|Target|eBay|Costco|Newegg|Home Depot", re.I))
        if store_el:
            for s in ["Amazon", "Walmart", "Best Buy", "Target", "eBay", "Costco", "Newegg", "Home Depot"]:
                if s.lower() in store_el.lower():
                    deal["store"] = s
                    break

        # Rating
        rating_match = re.search(r"([\d.]+)\s*(?:out of 5|/5|★)", card.get_text())
        if rating_match:
            try:
                deal["rating"] = float(rating_match.group(1))
            except ValueError:
                pass

        deal["condition"] = "New"
        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# DealNews scraper
# ============================================================

def scrape_dealnews(query="", max_results=40):
    """Scrape DealNews for deals."""
    if query:
        url = f"https://www.dealnews.com/search/?q={quote_plus(query)}"
    else:
        url = "https://www.dealnews.com/"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    for card in soup.find_all("div", class_=re.compile(r"content-card|deal-card|summary")):
        deal = {}
        title_el = card.find("a", class_=re.compile(r"title|heading")) or card.find("h2") or card.find("h3")
        if not title_el:
            continue
        deal["name"] = title_el.get_text().strip()
        if not deal["name"] or len(deal["name"]) < 10 or deal["name"] in seen:
            continue
        seen.add(deal["name"])

        href = title_el.get("href", "") if title_el.name == "a" else ""
        if not href:
            link = card.find("a", href=True)
            if link:
                href = link.get("href", "")
        if href:
            deal["url"] = href if href.startswith("http") else "https://www.dealnews.com" + href

        card_text = card.get_text()
        prices = re.findall(r"\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.5 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))
        elif deal.get("price") and deal.get("original_price"):
            deal["discount_pct"] = _clean_discount(round((1 - deal["price"] / deal["original_price"]) * 100))

        known_stores = ["Amazon", "Walmart", "Best Buy", "Target", "Costco", "Newegg",
                        "B&H", "Home Depot", "Lowe's", "Adorama", "Woot", "Dell", "HP", "Lenovo", "eBay"]
        for store in known_stores:
            if store.lower() in card_text.lower():
                deal["store"] = store
                break

        deal["condition"] = _detect_condition(card_text) or "New"

        if deal.get("price") or deal.get("discount_pct"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# Temu
# ============================================================

def scrape_temu(query="deals", max_results=60):
    """Scrape Temu search results."""
    url = f"https://www.temu.com/search_result.html?search_key={quote_plus(query)}"
    html = _fetch_page(url, scroll_count=4, wait_ms=3000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    # Temu product cards
    for card in soup.find_all(["div", "a"], attrs={"data-testid": re.compile(r"product|goods|item", re.I)}):
        deal = {}
        title_el = card.find(string=lambda t: t and len(t.strip()) > 15)
        if not title_el:
            continue
        deal["name"] = title_el.strip()[:120]
        if deal["name"] in seen or len(deal["name"]) < 10:
            continue
        seen.add(deal["name"])

        link = card.find("a", href=True) if card.name != "a" else card
        if link:
            href = link.get("href", "")
            deal["url"] = href if href.startswith("http") else "https://www.temu.com" + href

        card_text = card.get_text()
        prices = re.findall(r"\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.01 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"
                deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))

        deal["store"] = "Temu"
        deal["condition"] = "New"
        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    # Fallback: try generic product divs
    if not deals:
        for card in soup.find_all("div", class_=True):
            classes = " ".join(card.get("class", []))
            if "product" not in classes.lower() and "goods" not in classes.lower():
                continue
            text = card.get_text(separator=" ")
            if len(text) < 20 or len(text) > 500:
                continue
            deal = {}
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            name_candidates = [l for l in lines if len(l) > 15 and not l.startswith("$")]
            if name_candidates:
                deal["name"] = name_candidates[0][:120]
            else:
                continue
            if deal["name"] in seen:
                continue
            seen.add(deal["name"])

            prices = re.findall(r"\$([\d,]+\.?\d*)", text)
            prices = [float(p.replace(",", "")) for p in prices if 0.01 < float(p.replace(",", "")) < 50000]
            prices = sorted(set(prices))
            if prices:
                deal["price"] = prices[0]
                deal["price_str"] = f"${prices[0]:,.2f}"
                if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                    deal["original_price"] = prices[-1]
                    deal["original_price_str"] = f"${prices[-1]:,.2f}"
                    deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

            pct = re.search(r"(\d+)%\s*off", text, re.I)
            if pct:
                deal["discount_pct"] = _clean_discount(int(pct.group(1)))

            deal["store"] = "Temu"
            deal["condition"] = "New"
            deal["url"] = f"https://www.temu.com/search_result.html?search_key={quote_plus(query)}"
            if deal.get("price"):
                _validate_prices(deal)
                deals.append(deal)
            if len(deals) >= max_results:
                break

    return deals


# ============================================================
# AliExpress
# ============================================================

def scrape_aliexpress(query="deals", max_results=60):
    """Scrape AliExpress search results."""
    url = f"https://www.aliexpress.com/w/wholesale-{quote_plus(query)}.html"
    html = _fetch_page(url, scroll_count=4, wait_ms=3000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    for card in soup.find_all(["div", "a"], class_=re.compile(r"card|product|item|search-card", re.I)):
        deal = {}
        title_el = card.find("h1") or card.find("h3") or card.find(class_=re.compile(r"title|name", re.I))
        if title_el:
            deal["name"] = title_el.get_text().strip()[:120]
        if not deal.get("name") or len(deal["name"]) < 10 or deal["name"] in seen:
            continue
        seen.add(deal["name"])

        link = card.find("a", href=re.compile(r"aliexpress\.com/item/"))
        if not link:
            link = card if card.name == "a" else card.find("a", href=True)
        if link:
            href = link.get("href", "")
            deal["url"] = href if href.startswith("http") else "https:" + href if href.startswith("//") else "https://www.aliexpress.com" + href

        card_text = card.get_text()
        # AliExpress uses US$ or $ format
        prices = re.findall(r"(?:US\s*)?\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.01 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"
                deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))

        # Rating
        rating_match = re.search(r"([\d.]+)\s*(?:star|/5)", card_text, re.I)
        if rating_match:
            try:
                deal["rating"] = float(rating_match.group(1))
            except ValueError:
                pass

        # Orders/sold
        sold_match = re.search(r"([\d,]+)\+?\s*sold", card_text, re.I)
        if sold_match:
            deal["badge"] = sold_match.group(0).strip()

        deal["store"] = "AliExpress"
        deal["condition"] = "New"
        if deal.get("price"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# Woot (Amazon-owned daily deals)
# ============================================================

def scrape_woot(query="", max_results=40):
    """Scrape Woot deals."""
    if query:
        url = f"https://www.woot.com/search?query={quote_plus(query)}"
    else:
        url = "https://www.woot.com/"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    for card in soup.find_all(["div", "article", "section"], class_=re.compile(r"deal|product|item|offer|event", re.I)):
        deal = {}
        title_el = card.find("a", class_=re.compile(r"title|name", re.I)) or card.find("h2") or card.find("h3")
        if not title_el:
            title_el = card.find("a", href=re.compile(r"woot\.com/offers/"))
        if not title_el:
            continue
        deal["name"] = title_el.get_text().strip()[:120]
        if not deal["name"] or len(deal["name"]) < 10 or deal["name"] in seen:
            continue
        seen.add(deal["name"])

        link = title_el if title_el.name == "a" else card.find("a", href=True)
        if link:
            href = link.get("href", "")
            deal["url"] = href if href.startswith("http") else "https://www.woot.com" + href

        card_text = card.get_text()
        prices = re.findall(r"\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.5 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"
                deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))

        deal["store"] = "Woot"
        deal["condition"] = _detect_condition(card_text) or "New"
        if deal.get("price") or deal.get("discount_pct"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# TechBargains
# ============================================================

def scrape_techbargains(query="", max_results=40):
    """Scrape TechBargains deals."""
    if query:
        url = f"https://www.techbargains.com/search?q={quote_plus(query)}"
    else:
        url = "https://www.techbargains.com/deals"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    for card in soup.find_all(["div", "article"], class_=re.compile(r"deal|product|item|listing", re.I)):
        deal = {}
        title_el = card.find("a", class_=re.compile(r"title|heading", re.I)) or card.find("h2") or card.find("h3")
        if not title_el:
            continue
        deal["name"] = title_el.get_text().strip()[:120]
        if not deal["name"] or len(deal["name"]) < 10 or deal["name"] in seen:
            continue
        seen.add(deal["name"])

        link = title_el if title_el.name == "a" else card.find("a", href=True)
        if link:
            href = link.get("href", "")
            deal["url"] = href if href.startswith("http") else "https://www.techbargains.com" + href

        card_text = card.get_text()
        prices = re.findall(r"\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.5 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"
                deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))

        known_stores = ["Amazon", "Walmart", "Best Buy", "Target", "Costco", "Newegg",
                        "B&H", "Home Depot", "Adorama", "Dell", "HP", "Lenovo", "Apple"]
        for store in known_stores:
            if store.lower() in card_text.lower():
                deal["store"] = store
                break
        if "store" not in deal:
            deal["store"] = "TechBargains"

        deal["condition"] = _detect_condition(card_text) or "New"
        if deal.get("price") or deal.get("discount_pct"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


# ============================================================
# BensBargains
# ============================================================

def scrape_bensbargains(query="", max_results=40):
    """Scrape BensBargains deals."""
    if query:
        url = f"https://bensbargains.com/search/?q={quote_plus(query)}"
    else:
        url = "https://bensbargains.com/deals/"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    for card in soup.find_all(["div", "article", "li"], class_=re.compile(r"deal|bargain|product|item", re.I)):
        deal = {}
        title_el = card.find("a", class_=re.compile(r"title|name", re.I)) or card.find("h2") or card.find("h3")
        if not title_el:
            title_el = card.find("a", href=re.compile(r"/deal/"))
        if not title_el:
            continue
        deal["name"] = title_el.get_text().strip()[:120]
        if not deal["name"] or len(deal["name"]) < 10 or deal["name"] in seen:
            continue
        seen.add(deal["name"])

        link = title_el if title_el.name == "a" else card.find("a", href=True)
        if link:
            href = link.get("href", "")
            deal["url"] = href if href.startswith("http") else "https://bensbargains.com" + href

        card_text = card.get_text()
        prices = re.findall(r"\$([\d,]+\.?\d*)", card_text)
        prices = [float(p.replace(",", "")) for p in prices if 0.5 < float(p.replace(",", "")) < 50000]
        prices = sorted(set(prices))
        if prices:
            deal["price"] = prices[0]
            deal["price_str"] = f"${prices[0]:,.2f}"
            if len(prices) >= 2 and prices[-1] > prices[0] * 1.05:
                deal["original_price"] = prices[-1]
                deal["original_price_str"] = f"${prices[-1]:,.2f}"
                deal["discount_pct"] = _clean_discount(round((1 - prices[0] / prices[-1]) * 100))

        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = _clean_discount(int(pct.group(1)))

        known_stores = ["Amazon", "Walmart", "Best Buy", "Target", "Costco", "Newegg",
                        "B&H", "Home Depot", "Adorama", "Woot", "Dell", "HP", "Lenovo", "eBay"]
        for store in known_stores:
            if store.lower() in card_text.lower():
                deal["store"] = store
                break
        if "store" not in deal:
            deal["store"] = "BensBargains"

        deal["condition"] = _detect_condition(card_text) or "New"
        if deal.get("price") or deal.get("discount_pct"):
            _validate_prices(deal)
            deals.append(deal)
        if len(deals) >= max_results:
            break

    return deals


def get_deal_pages():
    return {"amazon": dict(AMAZON_DEAL_PAGES), "ebay": dict(EBAY_DEAL_PAGES)}

def deep_scan_deals(query="", min_discount=0, max_results=200, sort_by="cheapest"):
    """
    Search across ALL sources simultaneously:
    - Multiple pages of Amazon search results + deal pages
    - Multiple pages of eBay search results + deal pages
    - Slickdeals, Google Shopping, DealNews
    - Temu, AliExpress, Woot
    - TechBargains, BensBargains
    Returns combined, deduplicated, sorted results.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_deals = []
    seen_names = set()

    tasks = {}
    with ThreadPoolExecutor(max_workers=16) as pool:
        # Amazon: multiple search pages
        if query:
            for pg in range(1, 5):
                tasks[pool.submit(scrape_search_deals, query, 60, pg)] = f"Amazon page {pg}"
        tasks[pool.submit(scrape_goldbox_deals)] = "Amazon Goldbox"

        # eBay: multiple search pages + deal pages
        if query:
            for pg in range(1, 4):
                ebay_url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}&LH_BIN=1&_pgn={pg}"
                tasks[pool.submit(_scrape_ebay_page_safe, ebay_url, 60)] = f"eBay page {pg}"
        for page_name, page_url in EBAY_DEAL_PAGES.items():
            tasks[pool.submit(scrape_ebay_deals, page_url, 60)] = f"eBay {page_name}"

        # Slickdeals
        if query:
            tasks[pool.submit(scrape_multi_store_deals, query, 80)] = "Slickdeals"

        # Google Shopping
        if query:
            tasks[pool.submit(scrape_google_shopping, query, 80)] = "Google Shopping"
            tasks[pool.submit(scrape_google_shopping, query + " deals", 60)] = "Google deals"

        # DealNews
        tasks[pool.submit(scrape_dealnews, query or "", 40)] = "DealNews"

        # Temu
        if query:
            tasks[pool.submit(_safe_scrape, scrape_temu, query, 60)] = "Temu"

        # AliExpress
        if query:
            tasks[pool.submit(_safe_scrape, scrape_aliexpress, query, 60)] = "AliExpress"

        # Woot
        tasks[pool.submit(_safe_scrape, scrape_woot, query or "", 40)] = "Woot"

        # TechBargains
        tasks[pool.submit(_safe_scrape, scrape_techbargains, query or "", 40)] = "TechBargains"

        # BensBargains
        tasks[pool.submit(_safe_scrape, scrape_bensbargains, query or "", 40)] = "BensBargains"

        for future in as_completed(tasks):
            source_label = tasks[future]
            try:
                deals = future.result()
                for d in deals:
                    d.setdefault("store", source_label.split(" ")[0])
                all_deals.extend(deals)
            except Exception:
                pass

    # Deduplicate
    unique = []
    for d in all_deals:
        name_key = re.sub(r"[^a-z0-9]", "", d.get("name", "").lower())[:60]
        if name_key and name_key not in seen_names:
            seen_names.add(name_key)
            unique.append(d)

    # Filter by min discount
    if min_discount > 0:
        unique = [d for d in unique if (d.get("discount_pct") or 0) >= min_discount]

    # Filter by query words (loose match)
    if query:
        query_words = query.lower().split()
        filtered = [d for d in unique if any(w in d.get("name", "").lower() for w in query_words)]
        if len(filtered) >= 3:
            unique = filtered

    unique = _sort_deals(unique, sort_by)
    return unique[:max_results]


def _safe_scrape(func, *args, **kwargs):
    """Wrapper that catches exceptions so thread pool doesn't crash."""
    try:
        return func(*args, **kwargs)
    except Exception:
        return []


def _scrape_ebay_page_safe(url, max_results=60):
    """Wrapper for _scrape_ebay_page that won't crash the thread pool."""
    try:
        return _scrape_ebay_page(url, max_results)
    except Exception:
        return []
