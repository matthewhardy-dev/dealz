"""Background scheduler for Deal Finder Pro.
Runs periodic scans, checks price drops, and triggers alerts.
"""
import os
import threading
import time
from datetime import datetime, timedelta

# Will be initialized with Flask app context
_app = None
_scheduler_thread = None
_running = False

# Default queries to scan on schedule
DEFAULT_SCAN_QUERIES = [
    "laptop deals", "headphones deals", "tv deals", "iphone deals",
    "gaming deals", "kitchen deals", "shoes deals", "tablet deals",
]

SCAN_INTERVAL_MINUTES = int(os.environ.get("SCAN_INTERVAL", "60"))


def init_scheduler(app):
    """Initialize and start the background scheduler."""
    global _app, _scheduler_thread, _running
    _app = app
    _running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    print(f"[Scheduler] Started — scanning every {SCAN_INTERVAL_MINUTES} minutes")


def stop_scheduler():
    global _running
    _running = False


def _scheduler_loop():
    """Main scheduler loop — runs scans and checks alerts."""
    # Wait 30 seconds after startup before first scan
    time.sleep(30)

    while _running:
        try:
            _run_scheduled_scans()
        except Exception as e:
            print(f"[Scheduler] Error in scan cycle: {e}")

        # Sleep for the interval
        for _ in range(SCAN_INTERVAL_MINUTES * 60):
            if not _running:
                return
            time.sleep(1)


def _run_scheduled_scans():
    """Run all scheduled scans — default queries + user saved searches."""
    from smartscraper.deals import scrape_search_deals, scrape_ebay_search_deals
    from database import db, cache_deals, check_alerts, PriceAlert, SavedSearch, User
    from notifications import process_alerts

    with _app.app_context():
        print(f"[Scheduler] Starting scan cycle at {datetime.utcnow().isoformat()}")

        # Collect queries: defaults + all active alert queries + saved searches
        queries = set(DEFAULT_SCAN_QUERIES)

        try:
            alerts = PriceAlert.query.filter_by(is_active=True).all()
            for alert in alerts:
                if alert.search_query:
                    queries.add(alert.search_query.lower())
        except Exception as e:
            print(f"[Scheduler] Error loading alerts: {e}")

        try:
            saved = SavedSearch.query.all()
            for s in saved:
                if s.query:
                    queries.add(s.query.lower())
        except Exception as e:
            print(f"[Scheduler] Error loading saved searches: {e}")

        total_deals = 0
        total_alerts = 0

        for query in queries:
            try:
                # Amazon
                deals = scrape_search_deals(query, max_results=20)
                if deals:
                    cache_deals(deals, query, "amazon")
                    triggered = check_alerts(deals)
                    if triggered:
                        total_alerts += process_alerts(triggered)
                    total_deals += len(deals)
            except Exception as e:
                print(f"[Scheduler] Amazon scan failed for '{query}': {e}")

            try:
                # eBay
                deals = scrape_ebay_search_deals(query, max_results=20)
                if deals:
                    cache_deals(deals, query, "ebay")
                    triggered = check_alerts(deals)
                    if triggered:
                        total_alerts += process_alerts(triggered)
                    total_deals += len(deals)
            except Exception as e:
                print(f"[Scheduler] eBay scan failed for '{query}': {e}")

            # Small delay between queries to be polite
            time.sleep(5)

        print(f"[Scheduler] Scan complete: {total_deals} deals cached, {total_alerts} alerts sent")
