"""GA4 tools exposed to the Claude agent.

Each tool returns structured JSON so the agent can reason about it.
Currently backed by mock_data; swap in google-analytics-data when ready.
"""
from __future__ import annotations
import datetime as _dt
import json
from typing import Any

import mock_data


def _parse_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


# ---- Tool definitions sent to Claude ----

TOOL_SCHEMAS = [
    {
        "name": "query_pageviews",
        "description": (
            "Get page view counts from Google Analytics. Use this for questions about traffic, "
            "visits, or how many people viewed pages. Can group by date (time-series) or by "
            "page path (which pages got the most views)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
                "group_by": {
                    "type": "string",
                    "enum": ["date", "page"],
                    "description": "Group by 'date' for a daily time series, or 'page' to see which pages got the most views.",
                },
            },
            "required": ["start_date", "end_date", "group_by"],
        },
    },
    {
        "name": "query_events",
        "description": (
            "Query raw GA4 events with counts and per-event user reach. Use ONLY for "
            "questions about specific event behavior (which events fired most, how many "
            "form_submits, etc.). DO NOT use this for active users, sessions, traffic "
            "totals, engagement, or bounce rate - those metrics live in the dedicated "
            "traffic/acquisition/engagement tools and will not match GA4 Reports if "
            "derived from event sums. Omit event_name to get all events ranked by count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
                "event_name": {
                    "type": "string",
                    "description": "Optional GA4 event name to filter to (e.g. 'purchase', 'form_submit').",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "analyze_user_journey",
        "description": (
            "Return the e-commerce funnel (Session Start -> Product View -> Add to Cart -> "
            "Begin Checkout -> Add Payment Info -> Purchase) with user counts, conversion rate "
            "from previous step, and drop-off percentages. Use this for questions about user "
            "journeys, drop-off, or where the funnel is leaking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_top_products",
        "description": (
            "Return top-performing products by views, with add-to-cart and purchase counts, "
            "revenue, and per-product conversion rates. Use for 'top products', 'best sellers', "
            "or product performance analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max products to return (default 10)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_form_performance",
        "description": (
            "Per-form performance across the website: views, submissions, conversion rate, and "
            "location. Use this for lead-form analysis, identifying best/worst performing forms, "
            "or finding forms that need optimization."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_data_coverage",
        "description": (
            "Return the date range covered by the available analytics data. Call this first if "
            "the user asks about a period and you're unsure whether data exists for it."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    # ---------- Frontend audit (live page fetch) ----------
    {
        "name": "audit_page",
        "description": (
            "Fetch a live page from the user's website and extract its structural and "
            "conversion signals: title, headings, content depth, all CTAs (text + "
            "element), every form (with field count, required fields, submit label), "
            "trust signals (testimonials, reviews, FAQ, urgency, guarantees), schema "
            "markup, viewport meta, image alt coverage, and link mix. **Pair this with "
            "GA4 behavioral data to give UX/UI/conversion recommendations** - e.g. a "
            "page with high bounce in GA4 plus an 8-field form and no reviews on audit "
            "= concrete fix. Pass a full URL or a path (e.g. '/products/abc') if "
            "SITE_BASE_URL is configured. NOTE: only sees server-rendered HTML, not "
            "JavaScript-injected content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url_or_path": {
                    "type": "string",
                    "description": "Full URL (https://...) or site-relative path like /products/x",
                },
            },
            "required": ["url_or_path"],
        },
    },
    # ---------- GA4 Acquisition / Engagement (matches Reports, not event sums) ----------
    {
        "name": "query_traffic_summary",
        "description": (
            "**Source of truth for active users, sessions, page views, engagement rate, "
            "bounce rate, and average session duration.** Returns account-level totals "
            "that exactly match GA4 Reports > Acquisition Overview and Engagement Overview. "
            "Use this for ANY question about 'how is my traffic doing', 'how many active "
            "users', 'sessions in May', 'engagement rate', 'bounce rate'. Do NOT derive "
            "these numbers from query_events - they will be ~1% off because of GA4's "
            "HyperLogLog approximation in event-level user counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_traffic_over_time",
        "description": (
            "Daily time series of active users, new users, sessions, page views, and "
            "engagement rate - matches what GA4 Reports show on the Acquisition trend "
            "chart. Use for trend questions ('week-over-week traffic', 'is traffic "
            "growing', 'when did the dip happen')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_acquisition_by_channel",
        "description": (
            "Per-channel session and user breakdown (Organic Search, Direct, Paid Search, "
            "Organic Social, Referral, etc.) - matches GA4 Reports > Acquisition > Traffic "
            "Acquisition. Includes sessions, share, active users, engagement rate, bounce "
            "rate, avg session duration. Use for channel mix questions or 'which channel "
            "is performing best/worst'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_acquisition_by_source_medium",
        "description": (
            "Per source/medium breakdown for deeper traffic-source analysis (e.g. "
            "'google / organic', 'facebook.com / referral', 'newsletter / email'). "
            "Use when channel-level data isn't granular enough or to find specific "
            "high/low-performing sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    # ---------- GA4 custom-dimension slicing ----------
    {
        "name": "query_events_breakdown",
        "description": (
            "Slice GA4 events by any combination of dimensions (including custom "
            "dimensions registered in GA4). Use when the user wants per-form / per-"
            "trigger / per-variant / per-pincode / per-anything breakdowns of events. "
            "Custom dimensions must be addressed as 'customEvent:<param>' "
            "(e.g. 'customEvent:form_id', 'customEvent:form_trigger', "
            "'customEvent:pincode_entered'). Standard dimensions include 'pagePath', "
            "'eventName', 'sessionDefaultChannelGroup', 'deviceCategory', 'country'. "
            "Returns event_count + unique users per combination. If a custom-dimension "
            "column comes back as '(not set)' for every row, that dimension isn't "
            "registered in GA4 yet - tell the user how to register it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "event_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: filter to these event names (e.g. ['form_view', 'form_submit'])",
                },
                "dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Dimensions to break down by. Default ['eventName']. Use customEvent:<name> for custom dimensions.",
                },
                "page_path_contains": {
                    "type": "string",
                    "description": "Optional: only include events where pagePath contains this substring (e.g. '/floor-tiles')",
                },
                "limit": {"type": "integer", "description": "Max rows (default 200)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_form_breakdown",
        "description": (
            "Targeted form analytics: views, starts, submits, submit-rate per (form_id, "
            "page_path) combination. Uses the form_view / form_start / form_submit events "
            "with the customEvent:form_id custom dimension. Use this for questions like "
            "'how is the ask-the-tile-expert form performing on the floor tile category "
            "page'. Supports filtering to a specific form_id or page substring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "form_id": {"type": "string", "description": "Optional: filter to one form_id"},
                "page_path_contains": {"type": "string", "description": "Optional: filter to pages containing this substring"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    # ---------- Google Ads ----------
    {
        "name": "query_google_ads_summary",
        "description": (
            "Account-level Google Ads totals for a date range: spend, impressions, clicks, "
            "conversions, conversion value (revenue), CTR, CPC, CPA, ROAS. Use this for "
            "high-level questions about Google Ads performance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_google_ads_campaigns",
        "description": (
            "Per-campaign Google Ads breakdown with spend, clicks, conversions, conversion "
            "value, CTR, CPC, CPA, ROAS. Use when comparing campaigns, finding wasted spend, "
            "or identifying underperformers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_google_ads_keywords",
        "description": (
            "Per-keyword Google Ads performance: impressions, clicks, CTR, CPC, conversions, "
            "ROAS, plus campaign and match type. Use for keyword-level analysis, finding "
            "negative keyword candidates, or identifying scaling opportunities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max keywords (default 25)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    # ---------- Meta Ads ----------
    {
        "name": "query_meta_ads_summary",
        "description": (
            "Account-level Meta Ads (Facebook/Instagram) totals: spend, impressions, reach, "
            "clicks, conversions, conversion value, CTR, CPM, CPC, CPA, ROAS. Use for "
            "high-level questions about Meta ads performance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_meta_ads_campaigns",
        "description": (
            "Per-campaign Meta Ads breakdown with objective, spend, reach, frequency, CTR, "
            "CPM, conversions, ROAS. Use when comparing campaigns, spotting frequency-cap "
            "issues, or finding poor-ROAS campaigns to pause."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_meta_ads_creatives",
        "description": (
            "Per-creative (per-ad) Meta Ads performance with format (image/video/carousel/dpa), "
            "spend, CTR, conversions, ROAS. Use for creative testing analysis, identifying "
            "winning ad formats, or finding fatigued creatives to retire."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["start_date", "end_date"],
        },
    },
]


# ---- Tool dispatch ----

def _ga4_live() -> bool:
    import ga4_client
    return ga4_client.is_active()


def run_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a tool by name and return a JSON string for the agent."""
    import ga4_client
    try:
        if name == "query_pageviews":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _ga4_live():
                if args["group_by"] == "date":
                    data = ga4_client.pageviews_by_date(args["start_date"], args["end_date"])
                    total = sum(r["page_views"] for r in data)
                    return json.dumps({"rows": data, "total_page_views": total,
                                       "days": len(data), "source": "ga4_live"})
                data = ga4_client.pageviews_by_page(args["start_date"], args["end_date"])
                return json.dumps({"rows": data, "total_pages": len(data), "source": "ga4_live"})
            if args["group_by"] == "date":
                data = mock_data.pageviews_by_date(start, end)
                total = sum(r["page_views"] for r in data)
                return json.dumps({"rows": data, "total_page_views": total, "days": len(data)})
            else:
                data = mock_data.pageviews_by_page(start, end)
                return json.dumps({"rows": data, "total_pages": len(data)})

        if name == "query_events":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _ga4_live():
                data = ga4_client.events(args["start_date"], args["end_date"], args.get("event_name"))
                return json.dumps({"rows": data, "source": "ga4_live"})
            data = mock_data.events(start, end, args.get("event_name"))
            return json.dumps({"rows": data})

        if name == "analyze_user_journey":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _ga4_live():
                data = ga4_client.user_journey_funnel(args["start_date"], args["end_date"])
            else:
                data = mock_data.user_journey_funnel(start, end)
            if not data:
                return json.dumps({
                    "funnel": [],
                    "note": "No funnel events (view_item/add_to_cart/begin_checkout/purchase) "
                            "found in this property for the period. E-commerce event tracking "
                            "may not be set up.",
                })
            # surface biggest drop for convenience
            biggest_drop = max(data[1:], key=lambda s: s["drop_off_from_previous_pct"]) if len(data) > 1 else data[0]
            return json.dumps({
                "funnel": data,
                "biggest_drop_off": {
                    "between_steps": f"{biggest_drop['step']-1} -> {biggest_drop['step']}",
                    "step_name": biggest_drop["name"],
                    "drop_off_pct": biggest_drop["drop_off_from_previous_pct"],
                },
                "source": "ga4_live" if _ga4_live() else "mock",
            })

        if name == "query_top_products":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            limit = int(args.get("limit", 10))
            if _ga4_live():
                data = ga4_client.top_products(args["start_date"], args["end_date"], limit=limit)
                if not data:
                    return json.dumps({
                        "rows": [],
                        "note": "No item/e-commerce data found. The GA4 property may not have "
                                "e-commerce events (view_item with items[]) implemented.",
                    })
                return json.dumps({"rows": data, "source": "ga4_live"})
            data = mock_data.top_products(start, end, limit=limit)
            return json.dumps({"rows": data})

        if name == "query_form_performance":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _ga4_live():
                data = ga4_client.form_performance(args["start_date"], args["end_date"])
                if not data:
                    return json.dumps({
                        "rows": [],
                        "note": "No form events (form_start/form_submit/generate_lead) found. "
                                "Enable Enhanced Measurement > Form interactions in GA4, or "
                                "push custom form events via GTM.",
                    })
            else:
                data = mock_data.form_performance(start, end)
            best = max(data, key=lambda r: r["conversion_rate_pct"])
            worst = min(data, key=lambda r: r["conversion_rate_pct"])
            return json.dumps({
                "rows": data,
                "best_form": best,
                "worst_form": worst,
                "source": "ga4_live" if _ga4_live() else "mock",
            })

        if name == "get_data_coverage":
            if _ga4_live():
                return json.dumps({
                    "google_analytics": {
                        "source": "ga4_live",
                        "property_id": __import__("os").environ.get("GA4_PROPERTY_ID"),
                        "note": "Live GA4 property. Data typically retained 14 months.",
                    },
                    "google_ads": {"source": "mock",
                                   "data_start": mock_data.DATA_START.isoformat(),
                                   "data_end": mock_data.DATA_END.isoformat()},
                    "meta_ads": {"source": "mock",
                                 "data_start": mock_data.DATA_START.isoformat(),
                                 "data_end": mock_data.DATA_END.isoformat()},
                })
            return json.dumps({
                "data_start": mock_data.DATA_START.isoformat(),
                "data_end": mock_data.DATA_END.isoformat(),
                "source": "mock",
                "connected_sources": ["google_analytics", "google_ads", "meta_ads"],
                "note": "Synthetic data for demo. Swap in real APIs by editing tools.py.",
            })

        # ---------- Frontend audit ----------
        if name == "audit_page":
            import frontend_audit
            data = frontend_audit.audit_page(args["url_or_path"])
            return json.dumps(data)

        # ---------- GA4 Reports-aligned (acquisition / engagement) ----------
        if name == "query_traffic_summary":
            if not _ga4_live():
                return json.dumps({
                    "note": "Traffic summary needs the live GA4 connection. "
                            "In demo mode, use query_pageviews instead."
                })
            return json.dumps(ga4_client.traffic_summary(args["start_date"], args["end_date"]))

        if name == "query_traffic_over_time":
            if not _ga4_live():
                return json.dumps({
                    "note": "Traffic-over-time needs the live GA4 connection. "
                            "In demo mode, use query_pageviews with group_by=date."
                })
            data = ga4_client.traffic_over_time(args["start_date"], args["end_date"])
            return json.dumps({"rows": data, "days": len(data), "source": "ga4_live"})

        if name == "query_acquisition_by_channel":
            if not _ga4_live():
                return json.dumps({"note": "Acquisition-by-channel needs the live GA4 connection."})
            data = ga4_client.acquisition_by_channel(args["start_date"], args["end_date"])
            return json.dumps({"rows": data, "channels": len(data), "source": "ga4_live"})

        if name == "query_acquisition_by_source_medium":
            if not _ga4_live():
                return json.dumps({"note": "Acquisition-by-source needs the live GA4 connection."})
            limit = int(args.get("limit", 25))
            data = ga4_client.acquisition_by_source_medium(args["start_date"], args["end_date"], limit=limit)
            return json.dumps({"rows": data, "sources": len(data), "source": "ga4_live"})

        # ---------- GA4 custom-dimension slicing ----------
        if name == "query_events_breakdown":
            if not _ga4_live():
                return json.dumps({"note": "Custom-dimension event slicing needs the live GA4 connection."})
            data = ga4_client.events_breakdown(
                args["start_date"], args["end_date"],
                event_names=args.get("event_names"),
                dimensions=args.get("dimensions") or ["eventName"],
                page_path_contains=args.get("page_path_contains"),
                limit=int(args.get("limit", 200)),
            )
            return json.dumps({"rows": data, "count": len(data), "source": "ga4_live"})

        if name == "query_form_breakdown":
            if not _ga4_live():
                return json.dumps({"note": "Form breakdown needs the live GA4 connection."})
            data = ga4_client.form_breakdown(
                args["start_date"], args["end_date"],
                form_id=args.get("form_id"),
                page_path_contains=args.get("page_path_contains"),
            )
            data["source"] = "ga4_live"
            return json.dumps(data)

        # ---------- Google Ads ----------
        if name == "query_google_ads_summary":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            return json.dumps(mock_data.google_ads_summary(start, end))

        if name == "query_google_ads_campaigns":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            data = mock_data.google_ads_campaigns(start, end)
            spending = [c for c in data if c["spend"] > 0]
            best = max(spending, key=lambda c: c["roas"]) if spending else None
            worst = min(spending, key=lambda c: c["roas"]) if spending else None
            return json.dumps({"rows": data, "best_roas": best, "worst_roas": worst})

        if name == "query_google_ads_keywords":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            limit = int(args.get("limit", 25))
            data = mock_data.google_ads_keywords(start, end, limit=limit)
            return json.dumps({"rows": data})

        # ---------- Meta Ads ----------
        if name == "query_meta_ads_summary":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            return json.dumps(mock_data.meta_ads_summary(start, end))

        if name == "query_meta_ads_campaigns":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            data = mock_data.meta_ads_campaigns(start, end)
            spending = [c for c in data if c["spend"] > 0]
            best = max(spending, key=lambda c: c["roas"]) if spending else None
            worst = min(spending, key=lambda c: c["roas"]) if spending else None
            return json.dumps({"rows": data, "best_roas": best, "worst_roas": worst})

        if name == "query_meta_ads_creatives":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            data = mock_data.meta_ads_creatives(start, end)
            best = data[0] if data else None
            worst = data[-1] if data else None
            return json.dumps({"rows": data, "best_creative": best, "worst_creative": worst})

        return json.dumps({"error": f"Unknown tool: {name}"})

    except KeyError as e:
        return json.dumps({"error": f"Missing required argument: {e}"})
    except ValueError as e:
        return json.dumps({"error": f"Invalid argument: {e}"})
    except Exception as e:  # GA4 API errors (auth, quota, bad property...)
        return json.dumps({
            "error": f"{type(e).__name__}: {e}",
            "hint": "If this is a GA4 permission/auth error, verify the service account "
                    "has Viewer access on the property, the Analytics Data API is enabled, "
                    "and GA4_PROPERTY_ID is the numeric property ID (not the G-XXXX measurement ID).",
        })
