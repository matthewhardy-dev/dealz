"""Deal Finder - Scrapes Amazon & eBay deals with prices, discounts, and direct links."""
import os
import re
from urllib.parse import urljoin
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

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fetch_simple(url):
    """Fallback fetcher using requests (no JS rendering)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"}
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    return res.text


def _fetch_page(url, scroll_count=4, wait_ms=2000):
    if not HAS_PLAYWRIGHT:
        return _fetch_simple(url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
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


def scrape_search_deals(query="deals", max_results=30):
    """Scrape Amazon search results for deals with product info and links."""
    url = f"https://www.amazon.com/s?k={query.replace(' ', '+')}"
    html = _fetch_page(url)
    soup = BeautifulSoup(html, "lxml")
    deals = []

    # Find product cards - they're in divs with data-component-type="s-search-result"
    cards = soup.find_all("div", attrs={"data-component-type": "s-search-result"})

    if not cards:
        # Fallback: find by data-asin
        cards = soup.find_all("div", attrs={"data-asin": True})
        cards = [c for c in cards if c.get("data-asin")]

    for card in cards[:max_results]:
        deal = {}
        asin = card.get("data-asin", "")
        if not asin:
            continue

        # Product link
        link_el = card.find("a", class_=re.compile("a-link-normal.*s-line-clamp"))
        if not link_el:
            link_el = card.find("a", href=re.compile(r"/dp/|/gp/"))
        if link_el:
            href = link_el.get("href", "")
            deal["url"] = urljoin("https://www.amazon.com", href)
        else:
            deal["url"] = f"https://www.amazon.com/dp/{asin}"

        # Product name from img alt or link text
        img = card.find("img", alt=True)
        if img and len(img["alt"]) > 10:
            deal["name"] = img["alt"]
        else:
            h2 = card.find("h2")
            if h2:
                deal["name"] = h2.get_text().strip()
        if "name" not in deal:
            continue

        # Clean up sponsored prefix
        name = deal["name"]
        for prefix in ["Sponsored Ad - ", "Sponsored "]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        deal["name"] = name

        # Price - look for the main price
        price_whole = card.find("span", class_="a-price-whole")
        price_frac = card.find("span", class_="a-price-fraction")
        if price_whole:
            whole = price_whole.get_text().strip().rstrip(".")
            frac = price_frac.get_text().strip() if price_frac else "00"
            deal["price"] = _parse_price(f"${whole}.{frac}")
            deal["price_str"] = f"${whole}.{frac}"

        # Original price (strikethrough)
        original_prices = card.find_all("span", class_="a-price")
        if len(original_prices) >= 2:
            orig = original_prices[1].find("span", class_="a-offscreen")
            if orig:
                deal["original_price"] = _parse_price(orig.get_text())
                deal["original_price_str"] = orig.get_text().strip()

        # Discount percentage
        discount_el = card.find(string=re.compile(r"\d+%\s*off", re.I))
        if discount_el:
            deal["discount_pct"] = _parse_percent(discount_el)
        elif deal.get("price") and deal.get("original_price") and deal["original_price"] > 0:
            deal["discount_pct"] = round((1 - deal["price"] / deal["original_price"]) * 100)

        # Rating
        rating_el = card.find("span", class_="a-icon-alt")
        if rating_el:
            rating_text = rating_el.get_text()
            rmatch = re.search(r"([\d.]+) out of", rating_text)
            if rmatch:
                deal["rating"] = float(rmatch.group(1))

        # Review count
        review_el = card.find("span", class_=re.compile("a-size-base.*s-underline-text"))
        if review_el:
            rtext = review_el.get_text().strip().replace(",", "")
            if rtext.isdigit():
                deal["reviews"] = int(rtext)

        # Deal badge
        badge = card.find(string=re.compile(r"Limited time|Lightning|Best Seller|Climate", re.I))
        if badge:
            deal["badge"] = badge.strip()

        deals.append(deal)

    # Sort by discount percentage (highest first)
    deals.sort(key=lambda d: d.get("discount_pct", 0), reverse=True)
    return deals


def scrape_goldbox_deals():
    """Scrape Amazon Today's Deals page with prices and discounts."""
    html = _fetch_page(AMAZON_DEAL_PAGES["todays_deals"], scroll_count=5, wait_ms=3000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen_names = set()

    # Find all offscreen spans — they contain "Deal Price: $X" and "List: $X" and product names
    offscreens = soup.find_all("span", class_="a-offscreen")

    i = 0
    while i < len(offscreens):
        text = offscreens[i].get_text().strip()

        # Look for "Deal Price: $X.XX" pattern
        if not text.startswith("Deal Price:"):
            i += 1
            continue

        deal = {}
        deal_price = _parse_price(text)
        if deal_price:
            deal["price"] = deal_price
            deal["price_str"] = f"${deal_price:,.2f}"

        # Next offscreen is usually "List: $X.XX"
        if i + 1 < len(offscreens):
            next_text = offscreens[i + 1].get_text().strip()
            if next_text.startswith("List:"):
                list_price = _parse_price(next_text)
                if list_price:
                    deal["original_price"] = list_price
                    deal["original_price_str"] = f"${list_price:,.2f}"
                    if deal.get("price") and list_price > deal["price"]:
                        deal["discount_pct"] = round((1 - deal["price"] / list_price) * 100)

        # Next offscreen after list price is usually the product name
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

        # Find the product link — walk up from the offscreen span to find a container with a link
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

        # Look for deal badge
        if container:
            badge_match = container.find(string=re.compile(r"Limited time deal|Lightning Deal|Deal of the Day", re.I))
            if badge_match:
                deal["badge"] = badge_match.strip()

        deals.append(deal)
        i += 1

    deals.sort(key=lambda d: d.get("discount_pct", 0), reverse=True)
    return deals


# ============================================================
# eBay Deal Finder
# ============================================================

EBAY_DEAL_PAGES = {
    "daily_deals": "https://www.ebay.com/deals",
    "tech_deals": "https://www.ebay.com/deals/tech",
    "fashion_deals": "https://www.ebay.com/deals/fashion",
    "home_deals": "https://www.ebay.com/deals/home-and-garden",
    "global_deals": "https://www.ebay.com/globaldeals",
}


def scrape_ebay_deals(url="https://www.ebay.com/deals", max_results=30):
    """Scrape eBay deals page for products with prices, discounts, and links."""
    html = _fetch_page(url, scroll_count=4, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    # Find all product links
    links = [l for l in soup.find_all("a", href=re.compile(r"ebay\.com/itm/"))
             if len(l.get_text().strip()) > 10]

    for link in links:
        name = link.get_text().strip()
        for suffix in ["Opens in a new window or tab", "Opens in a new window"]:
            name = name.replace(suffix, "").strip()
        if not name or name in seen or len(name) < 10:
            continue
        seen.add(name)

        deal = {"name": name, "url": link.get("href", "").split("?")[0]}

        # Walk up to find price container
        container = link.parent
        for _ in range(8):
            if container is None:
                break
            text = container.get_text()
            if "$" in text and len(text) < 3000:
                prices = [float(p.replace(",", "")) for p in re.findall(r"\$(\d[\d,]*\.\d{2})", text)]
                prices = sorted(set(p for p in prices if 0.5 < p < 50000))
                if prices:
                    deal["price"] = prices[0]
                    deal["price_str"] = f"${prices[0]:,.2f}"
                    if len(prices) >= 2 and prices[-1] > prices[0]:
                        deal["original_price"] = prices[-1]
                        deal["original_price_str"] = f"${prices[-1]:,.2f}"
                        deal["discount_pct"] = round((1 - prices[0] / prices[-1]) * 100)
                pct = re.search(r"(\d+)%\s*off", text, re.I)
                if pct:
                    deal["discount_pct"] = int(pct.group(1))
                if "free shipping" in text.lower():
                    deal["badge"] = "Free Shipping"
                break
            container = container.parent

        if deal.get("price"):
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals.sort(key=lambda d: d.get("discount_pct", 0), reverse=True)
    return deals


def scrape_ebay_search_deals(query="deals", max_results=30):
    """Scrape eBay search results for deals."""
    url = f"https://www.ebay.com/sch/i.html?_nkw={query.replace(' ', '+')}&LH_BIN=1"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen = set()

    # Find all product links with names
    links = [l for l in soup.find_all("a", href=re.compile(r"ebay\.com/itm/"))
             if len(l.get_text().strip()) > 20]

    for link in links:
        name = link.get_text().strip()
        for suffix in ["Opens in a new window or tab", "Opens in a new window"]:
            name = name.replace(suffix, "").strip()
        if not name or name in seen or name == "Shop on eBay":
            continue
        seen.add(name)

        deal = {"name": name, "url": link.get("href", "").split("?")[0]}

        # Get the card container — walk up to su-card-container or s-card
        card = link.parent
        for _ in range(5):
            if card is None:
                break
            classes = card.get("class", [])
            class_str = " ".join(classes)
            # Stop at the full card container, not sub-elements like __header
            if ("su-card-container" in classes and "__" not in class_str) or "s-card" in classes:
                break
            card = card.parent

        if card:
            text = card.get_text(separator=" | ")

            # Extract prices — skip shipping prices (prefixed with +$)
            # Pattern: $item_price | $was_price | +$shipping
            item_prices = re.findall(r"(?<!\+)\$(\d[\d,]*\.\d{2})", text)
            item_prices = [float(p.replace(",", "")) for p in item_prices]
            item_prices = [p for p in item_prices if 0.5 < p < 50000]

            if item_prices:
                deal["price"] = item_prices[0]
                deal["price_str"] = f"${item_prices[0]:,.2f}"
                if len(item_prices) >= 2 and item_prices[1] > item_prices[0]:
                    deal["original_price"] = item_prices[1]
                    deal["original_price_str"] = f"${item_prices[1]:,.2f}"
                    deal["discount_pct"] = round((1 - item_prices[0] / item_prices[1]) * 100)

            pct = re.search(r"(\d+)%\s*off", text, re.I)
            if pct:
                deal["discount_pct"] = int(pct.group(1))

            if "free delivery" in text.lower() or "free shipping" in text.lower():
                deal["badge"] = "Free Shipping"

        if deal.get("price"):
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals.sort(key=lambda d: d.get("discount_pct", 0), reverse=True)
    return deals


def get_deal_pages():
    """Return all deal page URLs."""
    return {"amazon": dict(AMAZON_DEAL_PAGES), "ebay": dict(EBAY_DEAL_PAGES)}


# ============================================================
# Multi-Store Deal Finder (via Slickdeals)
# ============================================================

def scrape_multi_store_deals(query="deals", max_results=30):
    """Scrape Slickdeals for the best deals across all stores (Amazon, Walmart, Best Buy, etc)."""
    url = f"https://slickdeals.net/newsearch.php?q={query.replace(' ', '+')}&searcharea=deals&r=1"
    html = _fetch_page(url, scroll_count=3, wait_ms=2000)
    soup = BeautifulSoup(html, "lxml")
    deals = []
    seen_urls = set()

    # Each deal is in a li.searchPageGrid__feedItem
    cards = soup.find_all("li", class_=re.compile(r"searchPageGrid__feedItem|feedItem"))
    if not cards:
        # Fallback: find dealCardListView divs
        cards = soup.find_all("div", class_=re.compile(r"dealCard"))

    for card in cards:
        # Get the deal thread link (first /f/ link with long text, no #post)
        links = card.find_all("a", href=re.compile(r"/f/\d+"))
        title_link = None
        for l in links:
            href = l.get("href", "")
            text = l.get_text().strip()
            # Skip comment links (#post), price-only text, and short usernames
            if "#post" in href:
                continue
            if text.startswith("$") or len(text) < 15:
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

        # Clean name: remove leading price/symbols, trailing price info
        name = re.sub(r"^[\$\d,.]+[:\s]*", "", name).strip()
        name = re.sub(r"^[\*|:]+\s*", "", name).strip()
        name = re.sub(r"\s*\+?\s*(?:Free Shipping|Free S/H|\+FS).*$", "", name, flags=re.I).strip()
        name = re.sub(r"\s*(?:w/\s*Prime|or on \$?\d+).*$", "", name, flags=re.I).strip()
        if not name or len(name) < 10:
            continue

        full_url = "https://slickdeals.net" + href if href.startswith("/") else href
        deal = {"name": name, "url": full_url}

        # Extract data from card text
        card_text = card.get_text(separator="\n")
        lines = [l.strip() for l in card_text.split("\n") if l.strip()]

        # Prices
        all_prices = re.findall(r"(?<!\+)\$(\d[\d,]*\.?\d*)", card_text)
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

        # Discount percentage
        pct = re.search(r"(\d+)%\s*off", card_text, re.I)
        if pct:
            deal["discount_pct"] = int(pct.group(1))
        elif deal.get("price") and deal.get("original_price") and deal["original_price"] > deal["price"]:
            deal["discount_pct"] = round((1 - deal["price"] / deal["original_price"]) * 100)

        # Store name from card text
        known_stores = ["Amazon", "Walmart", "Best Buy", "Target", "Costco", "Newegg",
                        "B&H", "Home Depot", "Lowe's", "Adorama", "Woot", "Sam's Club",
                        "Micro Center", "Apple", "Dell", "HP", "Lenovo", "eBay"]
        for store in known_stores:
            if store.lower() in card_text.lower():
                deal["store"] = store
                break

        if "free shipping" in card_text.lower() or "+FS" in card_text or "free s/h" in card_text.lower():
            deal["badge"] = "Free Shipping"

        if deal.get("price") or deal.get("discount_pct"):
            deals.append(deal)
        if len(deals) >= max_results:
            break

    deals.sort(key=lambda d: (-(d.get("discount_pct") or 0), d.get("price", 99999)))
    return deals
