"""Realistic mock GA4 data covering Mar 2026 - Jun 2026.

Shaped to mirror what the GA4 Data API would return. Includes seasonality,
a real funnel with a clear drop-off, and form/product performance variance
so the agent has something to analyze.
"""
from __future__ import annotations
import datetime as _dt
import random
from typing import Iterator

random.seed(42)


def _daterange(start: _dt.date, end: _dt.date) -> Iterator[_dt.date]:
    d = start
    while d <= end:
        yield d
        d += _dt.timedelta(days=1)


# Date range covered by the mock dataset
DATA_START = _dt.date(2026, 3, 1)
DATA_END = _dt.date(2026, 6, 4)


PAGES = [
    ("/", "Home"),
    ("/products", "Product Listing"),
    ("/products/ceramic-vase-large", "Large Ceramic Vase"),
    ("/products/handmade-bowl-set", "Handmade Bowl Set"),
    ("/products/terracotta-planter", "Terracotta Planter"),
    ("/products/blue-dinner-plates", "Blue Dinner Plates"),
    ("/products/mug-collection", "Artisan Mug Collection"),
    ("/products/serving-platter", "Serving Platter"),
    ("/about", "About Us"),
    ("/contact", "Contact"),
    ("/blog/how-to-style-ceramics", "Blog: Styling Ceramics"),
    ("/blog/ceramic-care-guide", "Blog: Care Guide"),
    ("/wholesale", "Wholesale Inquiry"),
    ("/quote-request", "Custom Quote"),
    ("/checkout", "Checkout"),
    ("/cart", "Cart"),
]

PRODUCTS = [
    ("ceramic-vase-large", "Large Ceramic Vase", 89.0),
    ("handmade-bowl-set", "Handmade Bowl Set (4pc)", 124.0),
    ("terracotta-planter", "Terracotta Planter", 45.0),
    ("blue-dinner-plates", "Blue Dinner Plates (6pc)", 156.0),
    ("mug-collection", "Artisan Mug Collection", 68.0),
    ("serving-platter", "Serving Platter", 78.0),
]

# Forms: (form_id, location, type)
FORMS = [
    ("contact_main", "/contact", "contact"),
    ("newsletter_footer", "site-wide footer", "newsletter"),
    ("wholesale_inquiry", "/wholesale", "lead"),
    ("custom_quote", "/quote-request", "lead"),
    ("product_question", "product pages", "product_inquiry"),
    ("popup_discount", "homepage popup", "newsletter"),
]


def _seasonal_factor(d: _dt.date) -> float:
    """Light seasonality - traffic dips on weekends slightly, peaks mid-week."""
    weekday = d.weekday()
    if weekday in (5, 6):
        return 0.78
    if weekday in (1, 2, 3):
        return 1.15
    return 1.0


def _month_trend(d: _dt.date) -> float:
    """Steady growth then a dip in late May."""
    if d.month == 3:
        return 0.85
    if d.month == 4:
        return 1.0
    if d.month == 5:
        return 1.18 if d.day < 20 else 0.92  # dip after May 20
    if d.month == 6:
        return 1.05
    return 1.0


def pageviews_by_date(start: _dt.date, end: _dt.date) -> list[dict]:
    """Total pageviews per date."""
    rows = []
    for d in _daterange(start, end):
        base = 2800
        views = int(base * _seasonal_factor(d) * _month_trend(d) * random.uniform(0.92, 1.08))
        rows.append({"date": d.isoformat(), "page_views": views})
    return rows


def pageviews_by_page(start: _dt.date, end: _dt.date) -> list[dict]:
    """Pageviews aggregated by page path for the range."""
    days = (end - start).days + 1
    if days <= 0:
        return []
    # weights roughly reflect a real ecom site
    weights = {
        "/": 0.22,
        "/products": 0.18,
        "/products/ceramic-vase-large": 0.06,
        "/products/handmade-bowl-set": 0.05,
        "/products/terracotta-planter": 0.045,
        "/products/blue-dinner-plates": 0.055,
        "/products/mug-collection": 0.04,
        "/products/serving-platter": 0.035,
        "/about": 0.04,
        "/contact": 0.025,
        "/blog/how-to-style-ceramics": 0.06,
        "/blog/ceramic-care-guide": 0.045,
        "/wholesale": 0.02,
        "/quote-request": 0.015,
        "/cart": 0.06,
        "/checkout": 0.04,
    }
    # total daily views averaged
    avg_total_views = sum(r["page_views"] for r in pageviews_by_date(start, end))
    rows = []
    for path, title in PAGES:
        w = weights.get(path, 0.01)
        views = int(avg_total_views * w * random.uniform(0.9, 1.1))
        rows.append({"page_path": path, "page_title": title, "page_views": views})
    rows.sort(key=lambda r: r["page_views"], reverse=True)
    return rows


def events(start: _dt.date, end: _dt.date, event_name: str | None = None) -> list[dict]:
    """GA4 events with counts and unique users."""
    days = (end - start).days + 1
    if days <= 0:
        return []
    base_sessions = sum(int(2800 * _seasonal_factor(d) * _month_trend(d) * 0.45) for d in _daterange(start, end))

    catalog = [
        ("page_view", base_sessions * 2.4, base_sessions),
        ("session_start", base_sessions, base_sessions),
        ("scroll", int(base_sessions * 1.6), int(base_sessions * 0.85)),
        ("view_item", int(base_sessions * 0.55), int(base_sessions * 0.42)),
        ("add_to_cart", int(base_sessions * 0.092), int(base_sessions * 0.078)),
        ("begin_checkout", int(base_sessions * 0.034), int(base_sessions * 0.031)),
        ("purchase", int(base_sessions * 0.012), int(base_sessions * 0.011)),
        ("form_view", int(base_sessions * 0.21), int(base_sessions * 0.18)),
        ("form_submit", int(base_sessions * 0.024), int(base_sessions * 0.022)),
        ("file_download", int(base_sessions * 0.008), int(base_sessions * 0.007)),
        ("click_outbound", int(base_sessions * 0.04), int(base_sessions * 0.035)),
        ("video_start", int(base_sessions * 0.07), int(base_sessions * 0.06)),
        ("newsletter_signup", int(base_sessions * 0.018), int(base_sessions * 0.017)),
    ]
    rows = [
        {"event_name": name, "event_count": int(count * random.uniform(0.95, 1.05)),
         "unique_users": int(users * random.uniform(0.95, 1.05))}
        for name, count, users in catalog
    ]
    if event_name:
        rows = [r for r in rows if r["event_name"].lower() == event_name.lower()]
    rows.sort(key=lambda r: r["event_count"], reverse=True)
    return rows


def user_journey_funnel(start: _dt.date, end: _dt.date) -> list[dict]:
    """E-commerce funnel showing the standard purchase path."""
    days = (end - start).days + 1
    base = sum(int(2800 * _seasonal_factor(d) * _month_trend(d)) for d in _daterange(start, end))

    # realistic drop-off
    sessions = base
    product_view = int(sessions * 0.42)
    add_to_cart = int(product_view * 0.18)
    begin_checkout = int(add_to_cart * 0.38)
    payment_info = int(begin_checkout * 0.71)
    purchase = int(payment_info * 0.48)  # the big drop is here

    steps = [
        {"step": 1, "name": "Session Start", "users": sessions, "conversion_from_previous_pct": 100.0},
        {"step": 2, "name": "Product View", "users": product_view,
         "conversion_from_previous_pct": round(product_view / sessions * 100, 1)},
        {"step": 3, "name": "Add to Cart", "users": add_to_cart,
         "conversion_from_previous_pct": round(add_to_cart / product_view * 100, 1)},
        {"step": 4, "name": "Begin Checkout", "users": begin_checkout,
         "conversion_from_previous_pct": round(begin_checkout / add_to_cart * 100, 1)},
        {"step": 5, "name": "Add Payment Info", "users": payment_info,
         "conversion_from_previous_pct": round(payment_info / begin_checkout * 100, 1)},
        {"step": 6, "name": "Purchase", "users": purchase,
         "conversion_from_previous_pct": round(purchase / payment_info * 100, 1)},
    ]
    for s in steps:
        s["drop_off_from_previous_pct"] = round(100 - s["conversion_from_previous_pct"], 1)
    return steps


def top_products(start: _dt.date, end: _dt.date, limit: int = 10) -> list[dict]:
    """Top viewed products with view, add-to-cart, and purchase counts."""
    days = (end - start).days + 1
    base = sum(int(2800 * _seasonal_factor(d) * _month_trend(d) * 0.42) for d in _daterange(start, end))
    # uneven distribution across products
    weights = [0.28, 0.22, 0.16, 0.14, 0.12, 0.08]
    rows = []
    for (sku, name, price), w in zip(PRODUCTS, weights):
        views = int(base * w * random.uniform(0.9, 1.1))
        # add-to-cart and purchase rates vary by product
        atc_rate = random.uniform(0.08, 0.16)
        purchase_rate = random.uniform(0.32, 0.58)
        add_to_cart = int(views * atc_rate)
        purchases = int(add_to_cart * purchase_rate)
        revenue = purchases * price
        rows.append({
            "product_id": sku,
            "product_name": name,
            "unit_price": price,
            "item_views": views,
            "add_to_carts": add_to_cart,
            "purchases": purchases,
            "revenue": round(revenue, 2),
            "view_to_cart_rate_pct": round(atc_rate * 100, 1),
            "cart_to_purchase_rate_pct": round(purchase_rate * 100, 1),
        })
    rows.sort(key=lambda r: r["item_views"], reverse=True)
    return rows[:limit]


def form_performance(start: _dt.date, end: _dt.date) -> list[dict]:
    """Per-form views, submissions, and conversion rate."""
    days = (end - start).days + 1
    base = sum(int(2800 * _seasonal_factor(d) * _month_trend(d) * 0.45) for d in _daterange(start, end))
    # crafted to show clear winners and losers
    profiles = {
        "contact_main":       {"view_share": 0.18, "conv_rate": 0.32},
        "newsletter_footer":  {"view_share": 0.42, "conv_rate": 0.024},  # low - footer blind spot
        "wholesale_inquiry":  {"view_share": 0.06, "conv_rate": 0.41},  # high intent, low traffic
        "custom_quote":       {"view_share": 0.04, "conv_rate": 0.38},
        "product_question":   {"view_share": 0.22, "conv_rate": 0.12},
        "popup_discount":     {"view_share": 0.08, "conv_rate": 0.058},  # popup fatigue
    }
    rows = []
    for form_id, location, ftype in FORMS:
        p = profiles[form_id]
        views = int(base * p["view_share"] * random.uniform(0.92, 1.08))
        submissions = int(views * p["conv_rate"] * random.uniform(0.9, 1.1))
        rows.append({
            "form_id": form_id,
            "form_location": location,
            "form_type": ftype,
            "form_views": views,
            "form_submissions": submissions,
            "conversion_rate_pct": round(submissions / views * 100, 2) if views else 0.0,
        })
    rows.sort(key=lambda r: r["conversion_rate_pct"], reverse=True)
    return rows
