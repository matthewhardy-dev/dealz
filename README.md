# 🏷️ Deal Finder

A deal-finding web app that scrapes Amazon and eBay for the best deals, sorted by biggest discount. Also includes an auto-learning web scraper engine.

## Features

- **Amazon Deal Finder** — search deals or browse Today's Deals (Goldbox) with prices, list prices, and % off
- **eBay Deal Finder** — search eBay Buy It Now deals or browse Daily/Tech deals
- **Auto-Learning Scraper** — give it a URL + sample data, it learns the rules and scrapes similar content
- **JavaScript Rendering** — Playwright-powered browser for JS-heavy sites like Amazon
- **Dark Theme UI** — modern responsive web interface
- **Clickable Results** — every deal links directly to the product page
- **Sorted by Discount** — biggest savings shown first

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install browser for JS rendering
python -m playwright install chromium

# Run the web app
python app.py

# Open http://localhost:5000
```

## Project Structure

```
smart-scraper/
├── smartscraper/          # Core library
│   ├── __init__.py        # Package init
│   ├── scraper.py         # Auto-learning scraper engine
│   ├── deals.py           # Amazon & eBay deal scrapers
│   └── utils.py           # Helper functions
├── templates/
│   └── index.html         # Web UI (dark theme)
├── models/                # Saved scraper models
├── app.py                 # Flask web server
├── cli.py                 # Command-line interface
├── setup.py               # Package config
├── requirements.txt       # Dependencies
└── README.md
```

## Usage

### Web UI

Run `python app.py` and open http://localhost:5000

- **Amazon tab** — search any product or hit "Today's Deals"
- **eBay tab** — search deals or browse Daily/Tech deals
- Every deal card is clickable → opens on Amazon/eBay

### Python API

```python
from smartscraper import SmartScraper

scraper = SmartScraper()
results = scraper.build("https://books.toscrape.com", ["A Light in the Attic"])
more = scraper.get_result_similar("https://books.toscrape.com/catalogue/page-2.html")
```

### Deal Finder API

```python
from smartscraper.deals import scrape_search_deals, scrape_goldbox_deals, scrape_ebay_search_deals

# Amazon search deals
deals = scrape_search_deals("laptop deals", max_results=20)

# Amazon Today's Deals
deals = scrape_goldbox_deals()

# eBay search deals
deals = scrape_ebay_search_deals("headphones", max_results=20)
```

### CLI

```bash
python cli.py build "https://example.com" -w "Sample Text" -s my_model
python cli.py scrape "https://example.com/page2" -m my_model
```

## Amazon Deal Pages

| Page | URL |
|------|-----|
| Today's Deals | amazon.com/gp/goldbox |
| Best Sellers | amazon.com/gp/bestsellers |
| Movers & Shakers | amazon.com/gp/movers-and-shakers |
| New Releases | amazon.com/gp/new-releases |
| Coupons | amazon.com/Amazon-Coupons/b?node=2231352011 |
| Outlet | amazon.com/outlet |
| Warehouse | amazon.com/warehouse |

## eBay Deal Pages

| Page | URL |
|------|-----|
| Daily Deals | ebay.com/deals |
| Tech Deals | ebay.com/deals/tech |
| Fashion Deals | ebay.com/deals/fashion |
| Home Deals | ebay.com/deals/home-and-garden |
| Global Deals | ebay.com/globaldeals |

## License

MIT
