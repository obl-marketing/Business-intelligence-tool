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
        or os.environ.get("GA4_SERVICE_ACCOUNT_FILE")
    )


def is_active() -> bool:
    return os.environ.get("DATA_SOURCE", "mock").lower() == "ga4" and is_configured()


@lru_cache(maxsize=1)
def _client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/analytics.readonly"]
    file_path = (os.environ.get("GA4_SERVICE_ACCOUNT_FILE") or "").strip()
    raw_json = (os.environ.get("GA4_SERVICE_ACCOUNT_JSON") or "").strip()
    # Prefer the file when a path is given - most reliable on a server, and
    # avoids any half-filled inline-JSON env var.
    if file_path:
        credentials = service_account.Credentials.from_service_account_file(
            file_path, scopes=scopes
        )
    elif raw_json:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(raw_json), scopes=scopes
        )
    else:
        raise RuntimeError(
            "Set GA4_SERVICE_ACCOUNT_FILE (path to the .json key file) or "
            "GA4_SERVICE_ACCOUNT_JSON (inline JSON)."
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


# Default e-commerce funnel stages, each with SYNONYMS so we match whatever
# the property actually calls the event (view_item vs product_view vs
# "product view" ...). This is a best-guess default; the agent can override
# it entirely by passing funnel_events.
FUNNEL_STAGES = [
    ("Session Start", ["session_start", "session start"]),
    ("Product View", ["view_item", "product_view", "product view", "view_product",
                       "productview", "product_viewed", "pdp_view", "view_product_detail"]),
    ("Add to Cart", ["add_to_cart", "add to cart", "addtocart", "cart_add",
                     "added_to_cart", "add_cart"]),
    ("Begin Checkout", ["begin_checkout", "begin checkout", "checkout_start",
                        "initiate_checkout", "start_checkout", "checkout"]),
    ("Add Payment Info", ["add_payment_info", "add payment info", "payment_info",
                         "add_shipping_info"]),
    ("Purchase", ["purchase", "order_complete", "transaction", "order_placed",
                  "order", "conversion", "checkout_complete"]),
]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def user_journey_funnel(start: str, end: str, funnel_events: list[str] | None = None) -> dict:
    """Open funnel: unique users per funnel step.

    - If `funnel_events` is given (a list of the property's real event names in
      order), the funnel is built from exactly those events.
    - Otherwise each default stage is matched against the property's actual
      events using a synonym list + fuzzy (alphanumeric) comparison, so custom
      event names like 'product view' still map to the Product View step.

    Returns a dict with the funnel, plus which event each step used, which
    stages had no matching event, and a sample of the property's real event
    names - so the agent can adapt rather than assume standard names.
    """
    rows = events(start, end)
    users_by_event = {r["event_name"]: r["unique_users"] for r in rows}
    # normalized lookup: alnum-lowered event name -> (real name, users)
    norm_lookup = {_norm(k): (k, v) for k, v in users_by_event.items()}

    if funnel_events:
        stages = [(name, [name]) for name in funnel_events]
    else:
        stages = FUNNEL_STAGES

    def _match(candidates):
        for c in candidates:
            hit = norm_lookup.get(_norm(c))
            if hit:
                return hit  # (real_event_name, users)
        return None

    steps = []
    unmatched = []
    prev_users = None
    step_no = 0
    for label, candidates in stages:
        hit = _match(candidates)
        if hit is None:
            unmatched.append(label)
            continue
        real_event, users = hit
        step_no += 1
        conv = 100.0 if prev_users in (None, 0) else round(users / prev_users * 100, 1)
        steps.append({
            "step": step_no,
            "name": label,
            "event_used": real_event,
            "users": users,
            "conversion_from_previous_pct": conv,
            "drop_off_from_previous_pct": round(100 - conv, 1),
        })
        prev_users = users

    result = {"funnel": steps}
    if unmatched:
        result["unmatched_stages"] = unmatched
        result["note"] = (
            "These funnel stages had no matching event in this property: "
            + ", ".join(unmatched)
            + ". The event may have a different name here. Check the available "
            "events below (and the user's Training notes), then call this tool "
            "again with funnel_events set to the real event names, or ask the "
            "user which event represents each missing step."
        )
    # Always expose the real event names so the agent can adapt.
    result["available_events"] = [r["event_name"] for r in rows][:60]
    return result


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


# ---------------------------------------------------------------
# Acquisition / Engagement (matches GA4 Reports, not event totals)
# ---------------------------------------------------------------

# These metrics align 1-to-1 with what the GA4 UI shows under
# Reports > Acquisition > Overview and Reports > Engagement > Overview.
# Source of truth for "active users", "sessions", "engagement rate" etc.

def traffic_summary(start: str, end: str) -> dict:
    """Account-level totals matching GA4 Reports > Acquisition Overview."""
    rows = _run_report(
        dimensions=[],
        # GA4 Data API allows at most 10 metrics per request.
        metrics=[
            "activeUsers",
            "newUsers",
            "totalUsers",
            "sessions",
            "engagedSessions",
            "screenPageViews",
            "engagementRate",
            "bounceRate",
            "averageSessionDuration",
            "sessionsPerUser",
        ],
        start_date=start,
        end_date=end,
    )
    if not rows:
        return {"note": "No traffic data for this range."}
    r = rows[0]
    return {
        "date_range": {"start": start, "end": end},
        "active_users": int(_num(r["activeUsers"])),
        "new_users": int(_num(r["newUsers"])),
        "total_users": int(_num(r["totalUsers"])),
        "sessions": int(_num(r["sessions"])),
        "engaged_sessions": int(_num(r["engagedSessions"])),
        "page_views": int(_num(r["screenPageViews"])),
        "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
        "bounce_rate_pct": round(_num(r["bounceRate"]) * 100, 2),
        "avg_session_duration_seconds": round(_num(r["averageSessionDuration"]), 1),
        "sessions_per_user": round(_num(r["sessionsPerUser"]), 2),
        "source_metric_note": (
            "Uses GA4 Data API metrics that match Reports > Acquisition Overview "
            "(activeUsers, sessions, engagementRate). NOT derived from event sums."
        ),
    }


def traffic_over_time(start: str, end: str) -> list[dict]:
    """Daily time series matching GA4 Reports."""
    rows = _run_report(
        ["date"],
        ["activeUsers", "newUsers", "sessions", "screenPageViews", "engagementRate"],
        start,
        end,
    )
    out = [
        {
            "date": _iso_date(r["date"]),
            "active_users": int(_num(r["activeUsers"])),
            "new_users": int(_num(r["newUsers"])),
            "sessions": int(_num(r["sessions"])),
            "page_views": int(_num(r["screenPageViews"])),
            "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
        }
        for r in rows
    ]
    out.sort(key=lambda r: r["date"])
    return out


def acquisition_by_channel(start: str, end: str) -> list[dict]:
    """Per-channel breakdown matching GA4 Reports > Acquisition > Traffic Acquisition.

    Uses sessionDefaultChannelGroup (the same dimension the GA4 UI uses there).
    """
    rows = _run_report(
        ["sessionDefaultChannelGroup"],
        [
            "sessions",
            "activeUsers",
            "engagedSessions",
            "engagementRate",
            "bounceRate",
            "averageSessionDuration",
            "screenPageViews",
        ],
        start,
        end,
        limit=50,
    )
    total_sessions = sum(int(_num(r["sessions"])) for r in rows) or 1
    out = []
    for r in rows:
        sessions = int(_num(r["sessions"]))
        out.append({
            "channel": r["sessionDefaultChannelGroup"] or "(unassigned)",
            "sessions": sessions,
            "share_pct": round(sessions / total_sessions * 100, 1),
            "active_users": int(_num(r["activeUsers"])),
            "engaged_sessions": int(_num(r["engagedSessions"])),
            "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
            "bounce_rate_pct": round(_num(r["bounceRate"]) * 100, 2),
            "avg_session_duration_seconds": round(_num(r["averageSessionDuration"]), 1),
            "page_views": int(_num(r["screenPageViews"])),
        })
    out.sort(key=lambda r: r["sessions"], reverse=True)
    return out


def acquisition_by_source_medium(start: str, end: str, limit: int = 25) -> list[dict]:
    """Per source / medium breakdown for deeper acquisition analysis."""
    rows = _run_report(
        ["sessionSourceMedium"],
        ["sessions", "activeUsers", "engagementRate", "bounceRate", "averageSessionDuration"],
        start,
        end,
        limit=limit,
    )
    out = []
    for r in rows:
        out.append({
            "source_medium": r["sessionSourceMedium"],
            "sessions": int(_num(r["sessions"])),
            "active_users": int(_num(r["activeUsers"])),
            "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
            "bounce_rate_pct": round(_num(r["bounceRate"]) * 100, 2),
            "avg_session_duration_seconds": round(_num(r["averageSessionDuration"]), 1),
        })
    out.sort(key=lambda r: r["sessions"], reverse=True)
    return out


def engagement_summary(start: str, end: str) -> dict:
    """Account-level engagement metrics matching GA4 Reports > Engagement."""
    rows = _run_report(
        dimensions=[],
        metrics=[
            "engagedSessions",
            "engagementRate",
            "userEngagementDuration",
            "averageSessionDuration",
            "eventsPerSession",
            "screenPageViewsPerSession",
            "sessionsPerUser",
            "screenPageViews",
            "eventCount",
        ],
        start_date=start,
        end_date=end,
    )
    if not rows:
        return {"note": "No engagement data for this range."}
    r = rows[0]
    return {
        "date_range": {"start": start, "end": end},
        "engaged_sessions": int(_num(r["engagedSessions"])),
        "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
        "user_engagement_duration_seconds": round(_num(r["userEngagementDuration"]), 1),
        "avg_session_duration_seconds": round(_num(r["averageSessionDuration"]), 1),
        "events_per_session": round(_num(r["eventsPerSession"]), 2),
        "page_views_per_session": round(_num(r["screenPageViewsPerSession"]), 2),
        "sessions_per_user": round(_num(r["sessionsPerUser"]), 2),
        "page_views": int(_num(r["screenPageViews"])),
        "event_count": int(_num(r["eventCount"])),
    }


# ---------------------------------------------------------------
# Flexible event slicing (custom dimensions)
# ---------------------------------------------------------------

# When you query a custom dimension via the Data API, the dimension
# name is "customEvent:<param_name>" (e.g. "customEvent:form_id").
# This wrapper lets the agent ask for arbitrary breakdowns once you
# register custom dimensions in GA4.

def events_breakdown(
    start: str,
    end: str,
    event_names: list[str] | None = None,
    dimensions: list[str] | None = None,
    page_path_contains: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Slice events by any combination of dimensions, with optional filters.

    `dimensions` accepts standard GA4 dimensions (pagePath, eventName,
    sessionDefaultChannelGroup, deviceCategory, country, ...) AND custom
    dimensions registered in GA4, addressed as "customEvent:<param_name>"
    (e.g. "customEvent:form_id", "customEvent:form_trigger").

    Returns rows with eventCount + totalUsers per dimension combination.
    """
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric,
        FilterExpression, FilterExpressionList, Filter,
    )

    dims = dimensions or ["eventName"]
    request = RunReportRequest(
        property=_property(),
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[Dimension(name=d) for d in dims],
        metrics=[Metric(name="eventCount"), Metric(name="totalUsers")],
        limit=limit,
    )

    filters: list[FilterExpression] = []
    if event_names:
        filters.append(FilterExpression(
            filter=Filter(field_name="eventName",
                          in_list_filter=Filter.InListFilter(values=event_names))
        ))
    if page_path_contains:
        filters.append(FilterExpression(
            filter=Filter(field_name="pagePath",
                          string_filter=Filter.StringFilter(
                              match_type=Filter.StringFilter.MatchType.CONTAINS,
                              value=page_path_contains,
                          ))
        ))
    if len(filters) == 1:
        request.dimension_filter = filters[0]
    elif len(filters) > 1:
        request.dimension_filter = FilterExpression(
            and_group=FilterExpressionList(expressions=filters)
        )

    response = _client().run_report(request)
    out = []
    for r in response.rows:
        row = {}
        for d, dv in zip(dims, r.dimension_values):
            # strip the "customEvent:" prefix in keys for cleaner JSON
            key = d.replace("customEvent:", "")
            row[key] = dv.value or "(not set)"
        row["event_count"] = int(_num(r.metric_values[0].value))
        row["users"] = int(_num(r.metric_values[1].value))
        out.append(row)
    out.sort(key=lambda r: r["event_count"], reverse=True)
    return out


def form_breakdown(
    start: str,
    end: str,
    form_id: str | None = None,
    page_path_contains: str | None = None,
) -> dict:
    """High-level form analytics using the form_view / form_start / form_submit
    triplet with a customEvent:form_id custom dimension if registered."""
    dims = ["customEvent:form_id", "pagePath", "eventName"]
    rows = events_breakdown(
        start, end,
        event_names=["form_view", "form_start", "form_submit", "generate_lead"],
        dimensions=dims,
        page_path_contains=page_path_contains,
    )

    bucket: dict[tuple[str, str], dict] = {}
    for r in rows:
        fid = r.get("form_id", "(unknown)") or "(unknown)"
        if form_id and fid != form_id:
            continue
        page = r.get("pagePath", "(unknown)")
        key = (fid, page)
        slot = bucket.setdefault(key, {
            "form_id": fid,
            "page_path": page,
            "views": 0, "starts": 0, "submits": 0, "unique_users": 0,
        })
        ev = r.get("eventName", "")
        if ev == "form_view":
            slot["views"] = r["event_count"]
            slot["unique_users"] = max(slot["unique_users"], r["users"])
        elif ev == "form_start":
            slot["starts"] = r["event_count"]
        elif ev in ("form_submit", "generate_lead"):
            slot["submits"] += r["event_count"]

    out = []
    for slot in bucket.values():
        v = slot["views"]
        s = slot["submits"]
        slot["submit_rate_pct"] = round(s / v * 100, 2) if v else 0.0
        out.append(slot)
    out.sort(key=lambda r: r["views"], reverse=True)
    return {
        "rows": out,
        "note": (
            "If form_id is empty everywhere, the customEvent:form_id custom "
            "dimension is not registered in GA4 yet. Register it under "
            "Admin > Custom definitions and have the site fire form_view / "
            "form_submit events with a form_id parameter."
        ) if all(r["form_id"] == "(unknown)" for r in out) else None,
    }


# ---------------------------------------------------------------
# Per-page metrics (engagement time, bounce, etc. for ONE page)
# ---------------------------------------------------------------

def page_metrics(
    start: str,
    end: str,
    page_path_contains: str,
    exact: bool = False,
) -> dict:
    """Engagement + traffic metrics for a specific page (or page group).

    Matches the per-page numbers GA4 shows under Reports > Engagement > Pages.
    `page_path_contains` filters pagePath (substring by default). Pass
    `exact=True` to match a single exact pagePath — use this when you want the
    numbers for ONE page and they must line up with a single row in the GA4 UI.
    A CONTAINS match spans every path containing the string (so '/tiles/floor-tiles'
    also pulls in '/tiles/floor-tiles-matt', '/tiles/floor-tiles/600x600', ...),
    which legitimately shows higher totals than a single UI row.

    Runs the aggregate query WITHOUT pagePath as a dimension so GA4 deduplicates
    activeUsers correctly — the same way the GA4 UI does. Summing per-page
    activeUsers rows would double-count users who visited multiple matching paths.
    """
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, FilterExpression, Filter,
    )

    match_type = (
        Filter.StringFilter.MatchType.EXACT if exact
        else Filter.StringFilter.MatchType.CONTAINS
    )
    path_filter = FilterExpression(
        filter=Filter(
            field_name="pagePath",
            string_filter=Filter.StringFilter(
                match_type=match_type,
                value=page_path_contains,
            ),
        )
    )

    metrics = [
        "activeUsers",
        "screenPageViews",
        "userEngagementDuration",
        "engagementRate",
        "bounceRate",
        "averageSessionDuration",
        "eventCount",
        "sessions",
    ]

    # Aggregate query WITHOUT pagePath dimension → GA4 deduplicates activeUsers
    # across all matching paths. This matches the GA4 UI "Pages and screens" totals.
    agg_response = _client().run_report(RunReportRequest(
        property=_property(),
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[],
        metrics=[Metric(name=m) for m in metrics],
        dimension_filter=path_filter,
        limit=1,
    ))

    if not agg_response.rows:
        return {
            "page_filter": page_path_contains,
            "note": f"No pages matched '{page_path_contains}' in this date range.",
        }

    agg = {}
    for m, mv in zip(metrics, agg_response.rows[0].metric_values):
        agg[m] = _num(mv.value)

    # Secondary query WITH pagePath dimension — only to list which paths matched.
    page_response = _client().run_report(RunReportRequest(
        property=_property(),
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews")],
        dimension_filter=path_filter,
        limit=1000,
    ))
    matched_paths = [r.dimension_values[0].value for r in page_response.rows]

    active_users = agg["activeUsers"]
    total_engagement_seconds = agg["userEngagementDuration"]
    avg_engagement_per_user = (
        round(total_engagement_seconds / active_users, 1) if active_users else 0.0
    )

    return {
        "page_filter": page_path_contains,
        "match_mode": "exact" if exact else "contains",
        "matched_pages": matched_paths[:25],
        "matched_page_count": len(matched_paths),
        "date_range": {"start": start, "end": end},
        "active_users": int(active_users),
        "page_views": int(agg["screenPageViews"]),
        "sessions": int(agg["sessions"]),
        "avg_engagement_time_per_user_seconds": avg_engagement_per_user,
        "avg_engagement_time_per_user_readable": _fmt_seconds(avg_engagement_per_user),
        "total_engagement_time_seconds": int(total_engagement_seconds),
        "engagement_rate_pct": round(agg["engagementRate"] * 100, 2),
        "avg_session_duration_seconds": round(agg["averageSessionDuration"], 1),
        "events": int(agg["eventCount"]),
        "metric_note": (
            "activeUsers = unique users across all matching paths (GA4-deduplicated, "
            "matches 'Pages and screens' UI). screenPageViews = total views summed "
            "across all matching paths. avg_engagement_time_per_user = "
            "userEngagementDuration / activeUsers. "
            + ("Matched exactly one path (exact=True)." if exact else
               f"CONTAINS match spanned {len(matched_paths)} path(s) - if this is "
               "higher than a single GA4 UI row, pass exact=True for that one path.")
        ),
    }


def _fmt_seconds(s: float) -> str:
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {sec}s"


def pages_engagement_ranked(start: str, end: str, limit: int = 25) -> list[dict]:
    """Per-page engagement-time leaderboard - find pages users spend most/least
    time on. Matches GA4 Reports > Engagement > Pages and screens."""
    rows = _run_report(
        ["pagePath", "pageTitle"],
        ["activeUsers", "screenPageViews", "userEngagementDuration",
         "engagementRate", "bounceRate"],
        start,
        end,
        limit=max(limit * 4, 100),
    )
    out = []
    for r in rows:
        au = _num(r["activeUsers"])
        eng = _num(r["userEngagementDuration"])
        out.append({
            "page_path": r["pagePath"],
            "page_title": r["pageTitle"],
            "active_users": int(au),
            "page_views": int(_num(r["screenPageViews"])),
            "avg_engagement_time_per_user_seconds": round(eng / au, 1) if au else 0.0,
            "avg_engagement_time_per_user_readable": _fmt_seconds(eng / au if au else 0),
            "engagement_rate_pct": round(_num(r["engagementRate"]) * 100, 2),
            "bounce_rate_pct": round(_num(r["bounceRate"]) * 100, 2),
        })
    # rank by views so the leaderboard is meaningful, drop tiny-traffic noise
    out = [r for r in out if r["active_users"] >= 1]
    out.sort(key=lambda r: r["page_views"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------
# Popup breakdown (popup_id custom dimension)
# ---------------------------------------------------------------

POPUP_VIEW_EVENTS = ["popup_view", "popup_shown", "popup_open", "popup_opened", "modal_open"]
POPUP_CLOSE_EVENTS = ["popup_close", "popup_closed", "popup_dismiss", "popup_dismissed", "modal_close"]
POPUP_SUBMIT_EVENTS = ["popup_submit", "popup_conversion", "popup_lead", "generate_lead"]


def popup_breakdown(
    start: str,
    end: str,
    popup_id: str | None = None,
    page_path_contains: str | None = None,
) -> dict:
    """Per-popup analytics: views, closes (rage-quits), submits, submit rate,
    aggregated per (popup_id, page_path). Uses the popup_view / popup_close /
    popup_submit family of events with a customEvent:popup_id dimension.

    Tries a broad set of common event names so it works regardless of
    whether your site uses popup_view vs popup_shown vs popup_open etc.
    """
    all_events = POPUP_VIEW_EVENTS + POPUP_CLOSE_EVENTS + POPUP_SUBMIT_EVENTS
    rows = events_breakdown(
        start, end,
        event_names=all_events,
        dimensions=["customEvent:popup_id", "pagePath", "eventName"],
        page_path_contains=page_path_contains,
        limit=1000,
    )

    bucket: dict[tuple[str, str], dict] = {}
    for r in rows:
        pid = r.get("popup_id", "(unknown)") or "(unknown)"
        if popup_id and pid != popup_id:
            continue
        page = r.get("pagePath", "(unknown)")
        key = (pid, page)
        slot = bucket.setdefault(key, {
            "popup_id": pid,
            "page_path": page,
            "views": 0, "closes": 0, "submits": 0, "unique_users": 0,
        })
        ev = r.get("eventName", "")
        if ev in POPUP_VIEW_EVENTS:
            slot["views"] += r["event_count"]
            slot["unique_users"] = max(slot["unique_users"], r["users"])
        elif ev in POPUP_CLOSE_EVENTS:
            slot["closes"] += r["event_count"]
        elif ev in POPUP_SUBMIT_EVENTS:
            slot["submits"] += r["event_count"]

    out = []
    for slot in bucket.values():
        v = slot["views"]
        slot["submit_rate_pct"] = round(slot["submits"] / v * 100, 2) if v else 0.0
        slot["close_rate_pct"] = round(slot["closes"] / v * 100, 2) if v else 0.0
        out.append(slot)
    out.sort(key=lambda r: r["views"], reverse=True)

    needs_setup = all(r["popup_id"] == "(unknown)" for r in out) and out
    note = None
    if not out:
        note = ("No popup events found. Either no popups fired in this range, or "
                "the site doesn't emit any of: " + ", ".join(all_events))
    elif needs_setup:
        note = ("popup_id is empty for every row. Two possible fixes: "
                "(1) register 'popup_id' as a custom dimension in GA4 "
                "(Admin > Custom definitions > Create custom dimension, "
                "Event parameter = 'popup_id', Scope = Event), and "
                "(2) ensure the site's dataLayer.push includes popup_id "
                "on every popup_view / popup_close / popup_submit event.")

    return {"rows": out, "note": note}
