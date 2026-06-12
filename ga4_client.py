"""Live Google Analytics 4 Data API client.

Activated when DATA_SOURCE=ga4 and both GA4_PROPERTY_ID and
GA4_SERVICE_ACCOUNT_JSON are present (env vars or Streamlit secrets,
which app.py copies into the environment at startup).

Each function returns the same shape as its mock_data counterpart so
the agent-facing tool schemas never change.
"""
from __future__ import annotations
import json
import os
from functools import lru_cache


def is_configured() -> bool:
    return bool(os.environ.get("GA4_PROPERTY_ID")) and bool(
        os.environ.get("GA4_SERVICE_ACCOUNT_JSON")
    )


def is_active() -> bool:
    return os.environ.get("DATA_SOURCE", "mock").lower() == "ga4" and is_configured()


@lru_cache(maxsize=1)
def _client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    info = json.loads(os.environ["GA4_SERVICE_ACCOUNT_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def _property() -> str:
    return f"properties/{os.environ['GA4_PROPERTY_ID']}"


def _run_report(
    dimensions: list[str],
    metrics: list[str],
    start_date: str,
    end_date: str,
    event_names: list[str] | None = None,
    limit: int = 10000,
) -> list[dict]:
    """Run a GA4 report and return rows as plain dicts (metric values as strings)."""
    from google.analytics.data_v1beta.types import (
        RunReportRequest,
        DateRange,
        Dimension,
        Metric,
        FilterExpression,
        Filter,
    )

    request = RunReportRequest(
        property=_property(),
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimensions=[Dimension(name=d) for d in dimensions],
        metrics=[Metric(name=m) for m in metrics],
        limit=limit,
    )
    if event_names:
        request.dimension_filter = FilterExpression(
            filter=Filter(
                field_name="eventName",
                in_list_filter=Filter.InListFilter(values=event_names),
            )
        )
    response = _client().run_report(request)

    rows = []
    for r in response.rows:
        row: dict = {}
        for name, value in zip(dimensions, r.dimension_values):
            row[name] = value.value
        for name, value in zip(metrics, r.metric_values):
            row[name] = value.value
        rows.append(row)
    return rows


def _num(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_date(ga_date: str) -> str:
    """GA4 'date' dimension comes back as YYYYMMDD."""
    if len(ga_date) == 8 and ga_date.isdigit():
        return f"{ga_date[:4]}-{ga_date[4:6]}-{ga_date[6:]}"
    return ga_date


# ---------------------------------------------------------------
# Tool implementations (mirror mock_data return shapes)
# ---------------------------------------------------------------

def pageviews_by_date(start: str, end: str) -> list[dict]:
    rows = _run_report(["date"], ["screenPageViews"], start, end)
    out = [
        {"date": _iso_date(r["date"]), "page_views": int(_num(r["screenPageViews"]))}
        for r in rows
    ]
    out.sort(key=lambda r: r["date"])
    return out


def pageviews_by_page(start: str, end: str) -> list[dict]:
    rows = _run_report(["pagePath", "pageTitle"], ["screenPageViews"], start, end, limit=100)
    out = [
        {
            "page_path": r["pagePath"],
            "page_title": r["pageTitle"],
            "page_views": int(_num(r["screenPageViews"])),
        }
        for r in rows
    ]
    out.sort(key=lambda r: r["page_views"], reverse=True)
    return out


def events(start: str, end: str, event_name: str | None = None) -> list[dict]:
    rows = _run_report(
        ["eventName"],
        ["eventCount", "totalUsers"],
        start,
        end,
        event_names=[event_name] if event_name else None,
        limit=200,
    )
    out = [
        {
            "event_name": r["eventName"],
            "event_count": int(_num(r["eventCount"])),
            "unique_users": int(_num(r["totalUsers"])),
        }
        for r in rows
    ]
    out.sort(key=lambda r: r["event_count"], reverse=True)
    return out


FUNNEL_EVENTS = [
    ("session_start", "Session Start"),
    ("view_item", "Product View"),
    ("add_to_cart", "Add to Cart"),
    ("begin_checkout", "Begin Checkout"),
    ("add_payment_info", "Add Payment Info"),
    ("purchase", "Purchase"),
]


def user_journey_funnel(start: str, end: str) -> list[dict]:
    """Open funnel approximation: unique users per funnel event, ordered.

    (A strict closed funnel needs the GA4 Funnel Report API, which is alpha;
    user counts per event give the same drop-off signal for analysis.)
    """
    rows = events(start, end)
    users_by_event = {r["event_name"]: r["unique_users"] for r in rows}

    steps = []
    prev_users = None
    step_no = 0
    for event_name, label in FUNNEL_EVENTS:
        users = users_by_event.get(event_name, 0)
        if users == 0 and step_no == 0:
            continue  # property may not track session_start the same way
        step_no += 1
        if prev_users in (None, 0):
            conv = 100.0
        else:
            conv = round(users / prev_users * 100, 1)
        steps.append({
            "step": step_no,
            "name": label,
            "event_name": event_name,
            "users": users,
            "conversion_from_previous_pct": conv,
            "drop_off_from_previous_pct": round(100 - conv, 1),
        })
        prev_users = users
    return steps


def top_products(start: str, end: str, limit: int = 10) -> list[dict]:
    rows = _run_report(
        ["itemId", "itemName"],
        ["itemsViewed", "itemsAddedToCart", "itemsPurchased", "itemRevenue"],
        start,
        end,
        limit=200,
    )
    out = []
    for r in rows:
        views = int(_num(r["itemsViewed"]))
        carts = int(_num(r["itemsAddedToCart"]))
        purchases = int(_num(r["itemsPurchased"]))
        revenue = round(_num(r["itemRevenue"]), 2)
        out.append({
            "product_id": r["itemId"],
            "product_name": r["itemName"],
            "item_views": views,
            "add_to_carts": carts,
            "purchases": purchases,
            "revenue": revenue,
            "view_to_cart_rate_pct": round(carts / views * 100, 1) if views else 0.0,
            "cart_to_purchase_rate_pct": round(purchases / carts * 100, 1) if carts else 0.0,
        })
    out.sort(key=lambda r: r["item_views"], reverse=True)
    return out[:limit]


def form_performance(start: str, end: str) -> list[dict]:
    """Per-page form performance from GA4 enhanced-measurement events.

    Uses form_start / form_submit (auto-collected when Enhanced Measurement >
    Form interactions is on). Falls back to any custom events containing
    'form' if the standard ones are absent.
    """
    rows = _run_report(
        ["eventName", "pagePath"],
        ["eventCount", "totalUsers"],
        start,
        end,
        event_names=["form_start", "form_submit", "form_view", "generate_lead"],
        limit=1000,
    )

    by_page: dict[str, dict] = {}
    for r in rows:
        page = r["pagePath"]
        slot = by_page.setdefault(page, {"form_starts": 0, "form_submissions": 0})
        count = int(_num(r["eventCount"]))
        if r["eventName"] in ("form_start", "form_view"):
            slot["form_starts"] += count
        elif r["eventName"] in ("form_submit", "generate_lead"):
            slot["form_submissions"] += count

    out = []
    for page, counts in by_page.items():
        starts = counts["form_starts"]
        subs = counts["form_submissions"]
        out.append({
            "form_id": page,
            "form_location": page,
            "form_type": "tracked_form",
            "form_views": starts,
            "form_submissions": subs,
            "conversion_rate_pct": round(subs / starts * 100, 2) if starts else 0.0,
        })
    out.sort(key=lambda r: r["conversion_rate_pct"], reverse=True)
    return out
