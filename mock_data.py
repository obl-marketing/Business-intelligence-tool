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


# ============================================================
# Google Ads mock data
# ============================================================

GOOGLE_ADS_CAMPAIGNS = [
    ("Brand - Search",          "search",       "active",   "brand"),
    ("Non-Brand - Ceramics",    "search",       "active",   "prospecting"),
    ("Non-Brand - Home Decor",  "search",       "active",   "prospecting"),
    ("Performance Max - Catalog", "pmax",       "active",   "prospecting"),
    ("Display Remarketing",     "display",      "active",   "remarketing"),
    ("YouTube - Brand Story",   "video",        "paused",   "awareness"),
    ("Shopping - All Products", "shopping",     "active",   "prospecting"),
]

# Per-campaign profile: daily spend baseline + CTR / CVR / ROAS bands
GOOGLE_ADS_PROFILES = {
    "Brand - Search":            {"daily_spend": 28,  "ctr": 0.092, "cvr": 0.068, "roas": 9.8},
    "Non-Brand - Ceramics":      {"daily_spend": 95,  "ctr": 0.042, "cvr": 0.022, "roas": 2.4},
    "Non-Brand - Home Decor":    {"daily_spend": 72,  "ctr": 0.038, "cvr": 0.018, "roas": 1.9},
    "Performance Max - Catalog": {"daily_spend": 140, "ctr": 0.025, "cvr": 0.031, "roas": 3.6},
    "Display Remarketing":       {"daily_spend": 38,  "ctr": 0.0048,"cvr": 0.041, "roas": 4.2},
    "YouTube - Brand Story":     {"daily_spend": 0,   "ctr": 0.012, "cvr": 0.004, "roas": 0.6},
    "Shopping - All Products":   {"daily_spend": 110, "ctr": 0.031, "cvr": 0.024, "roas": 2.8},
}

GOOGLE_ADS_KEYWORDS = [
    # (keyword, campaign, match_type)
    ("ceramic vase",            "Non-Brand - Ceramics", "broad"),
    ("handmade pottery",        "Non-Brand - Ceramics", "phrase"),
    ("terracotta planter",      "Non-Brand - Ceramics", "exact"),
    ("dinner plates set",       "Non-Brand - Home Decor", "phrase"),
    ("artisan mugs",            "Non-Brand - Home Decor", "broad"),
    ("modern home decor",       "Non-Brand - Home Decor", "broad"),
    ("orient ceramics",         "Brand - Search",        "exact"),
    ("orientceramcs",           "Brand - Search",        "broad"),  # misspelling
    ("buy ceramic bowl",        "Non-Brand - Ceramics", "phrase"),
    ("ceramic gift ideas",      "Non-Brand - Ceramics", "broad"),
    ("wedding registry pottery","Non-Brand - Home Decor", "phrase"),
    ("cheap pottery",           "Non-Brand - Ceramics", "broad"),
]


def google_ads_campaigns(start: _dt.date, end: _dt.date) -> list[dict]:
    """Per-campaign Google Ads performance for the range."""
    days = (end - start).days + 1
    if days <= 0:
        return []
    rows = []
    for name, ctype, status, intent in GOOGLE_ADS_CAMPAIGNS:
        p = GOOGLE_ADS_PROFILES[name]
        spend = p["daily_spend"] * days * random.uniform(0.92, 1.08)
        if spend == 0:
            rows.append({
                "campaign_name": name, "type": ctype, "status": status,
                "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0,
                "conversion_value": 0.0, "ctr_pct": 0.0, "cpc": 0.0, "cpa": 0.0, "roas": 0.0,
            })
            continue
        avg_cpc = {"search": 1.85, "pmax": 1.10, "display": 0.45, "video": 0.32, "shopping": 0.95}[ctype]
        avg_cpc *= random.uniform(0.92, 1.08)
        clicks = int(spend / avg_cpc)
        impressions = int(clicks / p["ctr"])
        conversions = int(clicks * p["cvr"])
        conversion_value = round(spend * p["roas"], 2)
        rows.append({
            "campaign_name": name,
            "type": ctype,
            "status": status,
            "intent": intent,
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "conversion_value": conversion_value,
            "ctr_pct": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "cpc": round(spend / clicks, 2) if clicks else 0.0,
            "cpa": round(spend / conversions, 2) if conversions else 0.0,
            "roas": round(conversion_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return rows


def google_ads_summary(start: _dt.date, end: _dt.date) -> dict:
    """Account-level totals + averages."""
    campaigns = google_ads_campaigns(start, end)
    total_spend = sum(c["spend"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_impr = sum(c["impressions"] for c in campaigns)
    total_conv = sum(c["conversions"] for c in campaigns)
    total_value = sum(c["conversion_value"] for c in campaigns)
    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "spend": round(total_spend, 2),
        "impressions": total_impr,
        "clicks": total_clicks,
        "conversions": total_conv,
        "conversion_value": round(total_value, 2),
        "ctr_pct": round(total_clicks / total_impr * 100, 2) if total_impr else 0.0,
        "avg_cpc": round(total_spend / total_clicks, 2) if total_clicks else 0.0,
        "cpa": round(total_spend / total_conv, 2) if total_conv else 0.0,
        "roas": round(total_value / total_spend, 2) if total_spend else 0.0,
        "active_campaigns": sum(1 for c in campaigns if c["status"] == "active"),
    }


def google_ads_keywords(start: _dt.date, end: _dt.date, limit: int = 25) -> list[dict]:
    """Per-keyword performance with a realistic spread."""
    days = (end - start).days + 1
    # variance factor per keyword to make some clearly outperform
    keyword_quality = {
        "ceramic vase": 1.0,
        "handmade pottery": 1.3,        # strong
        "terracotta planter": 1.5,      # strongest
        "dinner plates set": 1.1,
        "artisan mugs": 0.9,
        "modern home decor": 0.55,      # broad, weak
        "orient ceramics": 2.4,         # brand, very strong
        "orientceramcs": 1.8,           # brand misspelling
        "buy ceramic bowl": 1.2,
        "ceramic gift ideas": 0.7,
        "wedding registry pottery": 1.4,
        "cheap pottery": 0.35,          # bad-intent traffic
    }
    rows = []
    for kw, campaign, match in GOOGLE_ADS_KEYWORDS:
        q = keyword_quality.get(kw, 1.0)
        base_impr = int(280 * days * random.uniform(0.85, 1.15))
        impr = base_impr if "Brand" not in campaign else int(base_impr * 0.35)
        ctr = (0.08 if "Brand" in campaign else 0.038) * random.uniform(0.7, 1.2)
        clicks = int(impr * ctr)
        avg_cpc = (0.55 if "Brand" in campaign else 1.95) * random.uniform(0.85, 1.2)
        spend = round(clicks * avg_cpc, 2)
        cvr = (0.072 if "Brand" in campaign else 0.022) * q * random.uniform(0.7, 1.3)
        conversions = int(clicks * cvr)
        conv_value = round(spend * (8.0 if "Brand" in campaign else 1.0) * q * random.uniform(0.8, 1.25), 2)
        rows.append({
            "keyword": kw,
            "match_type": match,
            "campaign": campaign,
            "impressions": impr,
            "clicks": clicks,
            "ctr_pct": round(clicks / impr * 100, 2) if impr else 0.0,
            "cpc": round(spend / clicks, 2) if clicks else 0.0,
            "spend": spend,
            "conversions": conversions,
            "conversion_value": conv_value,
            "cpa": round(spend / conversions, 2) if conversions else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return rows[:limit]


# ============================================================
# Meta Ads mock data
# ============================================================

META_ADS_CAMPAIGNS = [
    # (name, objective, status)
    ("Spring Conversion - Catalog",        "sales",         "active"),
    ("Retargeting - 30d Site Visitors",    "sales",         "active"),
    ("Cold Audience - Lookalike 1%",       "sales",         "active"),
    ("Awareness - Brand Story Video",      "awareness",     "active"),
    ("Traffic - Blog Articles",            "traffic",       "active"),
    ("Leads - Wholesale Inquiry",          "leads",         "active"),
    ("Engagement - Product UGC",           "engagement",    "paused"),
]

META_ADS_PROFILES = {
    "Spring Conversion - Catalog":     {"daily_spend": 165, "ctr": 0.014, "cvr": 0.022, "roas": 3.1, "cpm": 11.2},
    "Retargeting - 30d Site Visitors": {"daily_spend": 58,  "ctr": 0.028, "cvr": 0.045, "roas": 5.8, "cpm": 13.5},
    "Cold Audience - Lookalike 1%":    {"daily_spend": 220, "ctr": 0.011, "cvr": 0.012, "roas": 1.6, "cpm": 9.8},
    "Awareness - Brand Story Video":   {"daily_spend": 72,  "ctr": 0.008, "cvr": 0.003, "roas": 0.4, "cpm": 6.4},
    "Traffic - Blog Articles":         {"daily_spend": 35,  "ctr": 0.022, "cvr": 0.006, "roas": 0.9, "cpm": 5.2},
    "Leads - Wholesale Inquiry":       {"daily_spend": 48,  "ctr": 0.017, "cvr": 0.038, "roas": 0.0, "cpm": 12.1},
    "Engagement - Product UGC":        {"daily_spend": 0,   "ctr": 0.034, "cvr": 0.002, "roas": 0.3, "cpm": 4.8},
}

META_ADS_CREATIVES = [
    # (creative_name, format, campaign)
    ("Lifestyle Vase Hero",           "image",    "Spring Conversion - Catalog"),
    ("Dinner Set Carousel v2",        "carousel", "Spring Conversion - Catalog"),
    ("Pottery-in-use Video 15s",      "video",    "Spring Conversion - Catalog"),
    ("Retargeting - Last Viewed",     "dpa",      "Retargeting - 30d Site Visitors"),
    ("Founder Story Short",           "video",    "Awareness - Brand Story Video"),
    ("Studio B-roll Reel",            "video",    "Awareness - Brand Story Video"),
    ("Care Guide Blog Promo",         "image",    "Traffic - Blog Articles"),
    ("Cold Lookalike Carousel A",     "carousel", "Cold Audience - Lookalike 1%"),
    ("Cold Lookalike Static",         "image",    "Cold Audience - Lookalike 1%"),
    ("Wholesale Lead Form Hero",      "image",    "Leads - Wholesale Inquiry"),
    ("UGC Customer Reel",             "video",    "Engagement - Product UGC"),
]


def meta_ads_campaigns(start: _dt.date, end: _dt.date) -> list[dict]:
    days = (end - start).days + 1
    if days <= 0:
        return []
    rows = []
    for name, objective, status in META_ADS_CAMPAIGNS:
        p = META_ADS_PROFILES[name]
        spend = p["daily_spend"] * days * random.uniform(0.92, 1.08)
        if spend == 0:
            rows.append({
                "campaign_name": name, "objective": objective, "status": status,
                "spend": 0.0, "impressions": 0, "reach": 0, "clicks": 0,
                "conversions": 0, "conversion_value": 0.0,
                "ctr_pct": 0.0, "cpm": 0.0, "cpc": 0.0, "cpa": 0.0, "roas": 0.0,
            })
            continue
        impressions = int(spend / p["cpm"] * 1000)
        reach = int(impressions * random.uniform(0.42, 0.58))
        clicks = int(impressions * p["ctr"])
        conversions = int(clicks * p["cvr"])
        conv_value = round(spend * p["roas"], 2)
        rows.append({
            "campaign_name": name,
            "objective": objective,
            "status": status,
            "spend": round(spend, 2),
            "impressions": impressions,
            "reach": reach,
            "frequency": round(impressions / reach, 2) if reach else 0.0,
            "clicks": clicks,
            "conversions": conversions,
            "conversion_value": conv_value,
            "ctr_pct": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "cpm": round(spend / impressions * 1000, 2) if impressions else 0.0,
            "cpc": round(spend / clicks, 2) if clicks else 0.0,
            "cpa": round(spend / conversions, 2) if conversions else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda r: r["spend"], reverse=True)
    return rows


def meta_ads_summary(start: _dt.date, end: _dt.date) -> dict:
    campaigns = meta_ads_campaigns(start, end)
    total_spend = sum(c["spend"] for c in campaigns)
    total_impr = sum(c["impressions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_conv = sum(c["conversions"] for c in campaigns)
    total_value = sum(c["conversion_value"] for c in campaigns)
    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "spend": round(total_spend, 2),
        "impressions": total_impr,
        "clicks": total_clicks,
        "conversions": total_conv,
        "conversion_value": round(total_value, 2),
        "ctr_pct": round(total_clicks / total_impr * 100, 2) if total_impr else 0.0,
        "avg_cpc": round(total_spend / total_clicks, 2) if total_clicks else 0.0,
        "avg_cpm": round(total_spend / total_impr * 1000, 2) if total_impr else 0.0,
        "cpa": round(total_spend / total_conv, 2) if total_conv else 0.0,
        "roas": round(total_value / total_spend, 2) if total_spend else 0.0,
        "active_campaigns": sum(1 for c in campaigns if c["status"] == "active"),
    }


def meta_ads_creatives(start: _dt.date, end: _dt.date) -> list[dict]:
    """Per-creative performance for ad fatigue / creative testing analysis."""
    days = (end - start).days + 1
    # crafted spread so winners and losers are clear
    creative_quality = {
        "Lifestyle Vase Hero":         1.4,
        "Dinner Set Carousel v2":      1.7,   # top
        "Pottery-in-use Video 15s":    1.2,
        "Retargeting - Last Viewed":   1.6,
        "Founder Story Short":         0.45,
        "Studio B-roll Reel":          0.35,  # underperformer
        "Care Guide Blog Promo":       0.9,
        "Cold Lookalike Carousel A":   1.0,
        "Cold Lookalike Static":       0.7,
        "Wholesale Lead Form Hero":    1.3,
        "UGC Customer Reel":           0.6,
    }
    rows = []
    for cname, cformat, campaign in META_ADS_CREATIVES:
        q = creative_quality.get(cname, 1.0)
        camp_profile = META_ADS_PROFILES.get(campaign, {"daily_spend": 50, "ctr": 0.015, "cvr": 0.02, "roas": 2.0, "cpm": 10.0})
        # split campaign budget across creatives loosely
        spend = camp_profile["daily_spend"] * days * random.uniform(0.08, 0.32) * (0.7 if q < 0.7 else 1.0)
        if spend < 5:
            continue
        impressions = int(spend / camp_profile["cpm"] * 1000)
        ctr = camp_profile["ctr"] * q * random.uniform(0.8, 1.2)
        clicks = int(impressions * ctr)
        cvr = camp_profile["cvr"] * q * random.uniform(0.8, 1.2)
        conversions = int(clicks * cvr)
        conv_value = round(spend * camp_profile["roas"] * q * random.uniform(0.85, 1.15), 2)
        rows.append({
            "creative_name": cname,
            "format": cformat,
            "campaign": campaign,
            "spend": round(spend, 2),
            "impressions": impressions,
            "clicks": clicks,
            "ctr_pct": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "conversions": conversions,
            "conversion_value": conv_value,
            "cpa": round(spend / conversions, 2) if conversions else 0.0,
            "roas": round(conv_value / spend, 2) if spend else 0.0,
        })
    rows.sort(key=lambda r: r["roas"], reverse=True)
    return rows
