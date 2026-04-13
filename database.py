"""Database models for Deal Finder Pro — SQLite with SQLAlchemy."""
import os
import json
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

DB_PATH = os.path.join(os.path.dirname(__file__), "dealfinder.db")


def init_db(app):
    """Initialize database with Flask app."""
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()


class Deal(db.Model):
    """Cached deal from any source."""
    __tablename__ = "deals"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(1000))
    price = db.Column(db.Float)
    original_price = db.Column(db.Float)
    discount_pct = db.Column(db.Integer)
    store = db.Column(db.String(100))
    source = db.Column(db.String(50))  # amazon, ebay, slickdeals, etc.
    condition = db.Column(db.String(50))
    rating = db.Column(db.Float)
    reviews = db.Column(db.Integer)
    badge = db.Column(db.String(200))
    coupon = db.Column(db.String(200))
    image_url = db.Column(db.String(1000))
    deal_age = db.Column(db.String(50))
    query = db.Column(db.String(200))  # what search found this
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        d = {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "price": self.price,
            "price_str": f"${self.price:,.2f}" if self.price else None,
            "original_price": self.original_price,
            "original_price_str": f"${self.original_price:,.2f}" if self.original_price else None,
            "discount_pct": self.discount_pct,
            "store": self.store,
            "source": self.source,
            "condition": self.condition,
            "rating": self.rating,
            "reviews": self.reviews,
            "badge": self.badge,
            "coupon": self.coupon,
            "deal_age": self.deal_age,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }
        return d


class PriceHistory(db.Model):
    """Price history for a deal over time."""
    __tablename__ = "price_history"
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    price = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    deal = db.relationship("Deal", backref=db.backref("price_history", lazy=True))


class User(db.Model):
    """User account."""
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True)
    phone = db.Column(db.String(20))
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "phone": self.phone,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PriceAlert(db.Model):
    """Alert: notify user when a product drops below target price."""
    __tablename__ = "price_alerts"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    search_query = db.Column(db.String(200), nullable=False)
    target_price = db.Column(db.Float)
    min_discount = db.Column(db.Integer)
    notify_email = db.Column(db.Boolean, default=True)
    notify_sms = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    last_notified = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("alerts", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "query": self.search_query,
            "target_price": self.target_price,
            "min_discount": self.min_discount,
            "notify_email": self.notify_email,
            "notify_sms": self.notify_sms,
            "is_active": self.is_active,
            "last_notified": self.last_notified.isoformat() if self.last_notified else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class SavedSearch(db.Model):
    """Saved search for quick re-run."""
    __tablename__ = "saved_searches"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(100))
    query = db.Column(db.String(200), nullable=False)
    source = db.Column(db.String(50))  # amazon, ebay, google, deepscan
    filters_json = db.Column(db.Text)  # JSON string of filters
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("saved_searches", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "query": self.query,
            "source": self.source,
            "filters": json.loads(self.filters_json) if self.filters_json else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Wishlist(db.Model):
    """User's wishlist items."""
    __tablename__ = "wishlist"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"))
    name = db.Column(db.String(500))
    url = db.Column(db.String(1000))
    target_price = db.Column(db.Float)
    current_price = db.Column(db.Float)
    added_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("wishlist", lazy=True))
    deal = db.relationship("Deal", backref=db.backref("wishlisted_by", lazy=True))

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "target_price": self.target_price,
            "current_price": self.current_price,
            "added_at": self.added_at.isoformat() if self.added_at else None,
        }


# ---- Helper functions ----

def cache_deals(deals_list, query="", source=""):
    """Cache scraped deals to database and record price history."""
    for d in deals_list:
        # Find existing or create new
        existing = Deal.query.filter_by(
            name=d.get("name", "")[:500],
            store=d.get("store", source)
        ).first()

        if existing:
            # Update price if changed
            if d.get("price") and existing.price != d["price"]:
                ph = PriceHistory(deal_id=existing.id, price=d["price"])
                db.session.add(ph)
            existing.price = d.get("price", existing.price)
            existing.original_price = d.get("original_price", existing.original_price)
            existing.discount_pct = d.get("discount_pct", existing.discount_pct)
            existing.last_seen = datetime.utcnow()
            existing.is_active = True
        else:
            deal = Deal(
                name=d.get("name", "")[:500],
                url=d.get("url"),
                price=d.get("price"),
                original_price=d.get("original_price"),
                discount_pct=d.get("discount_pct"),
                store=d.get("store", source),
                source=source,
                condition=d.get("condition"),
                rating=d.get("rating"),
                reviews=d.get("reviews"),
                badge=d.get("badge"),
                coupon=d.get("coupon"),
                deal_age=d.get("deal_age"),
                query=query[:200] if query else None,
            )
            db.session.add(deal)
            db.session.flush()
            if d.get("price"):
                ph = PriceHistory(deal_id=deal.id, price=d["price"])
                db.session.add(ph)

    db.session.commit()


def get_price_history(deal_id):
    """Get price history for a deal."""
    records = PriceHistory.query.filter_by(deal_id=deal_id).order_by(
        PriceHistory.recorded_at.asc()
    ).all()
    return [{"price": r.price, "date": r.recorded_at.isoformat()} for r in records]


def check_alerts(deals_list):
    """Check if any deals match active price alerts. Returns list of triggered alerts."""
    triggered = []
    active_alerts = PriceAlert.query.filter_by(is_active=True).all()

    for alert in active_alerts:
        # Don't re-notify within 24 hours
        if alert.last_notified and (datetime.utcnow() - alert.last_notified) < timedelta(hours=24):
            continue

        query_words = alert.search_query.lower().split()
        for deal in deals_list:
            name_lower = deal.get("name", "").lower()
            if not any(w in name_lower for w in query_words):
                continue

            price_match = not alert.target_price or (deal.get("price", 99999) <= alert.target_price)
            disc_match = not alert.min_discount or (deal.get("discount_pct", 0) >= alert.min_discount)

            if price_match and disc_match:
                triggered.append({
                    "alert": alert,
                    "deal": deal,
                    "user": alert.user,
                })
                alert.last_notified = datetime.utcnow()
                break

    db.session.commit()
    return triggered
