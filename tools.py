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
            "Query GA4 events with counts and unique users. Use this to understand user behavior "
            "(clicks, scrolls, video starts, form_submit, purchase, view_item, etc.). Omit "
            "event_name to get all events ranked by count."
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
]


# ---- Tool dispatch ----

def run_tool(name: str, args: dict[str, Any]) -> str:
    """Execute a tool by name and return a JSON string for the agent."""
    try:
        if name == "query_pageviews":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
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
            data = mock_data.events(start, end, args.get("event_name"))
            return json.dumps({"rows": data})

        if name == "analyze_user_journey":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            data = mock_data.user_journey_funnel(start, end)
            # surface biggest drop for convenience
            biggest_drop = max(data[1:], key=lambda s: s["drop_off_from_previous_pct"])
            return json.dumps({
                "funnel": data,
                "biggest_drop_off": {
                    "between_steps": f"{biggest_drop['step']-1} -> {biggest_drop['step']}",
                    "step_name": biggest_drop["name"],
                    "drop_off_pct": biggest_drop["drop_off_from_previous_pct"],
                },
            })

        if name == "query_top_products":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            limit = int(args.get("limit", 10))
            data = mock_data.top_products(start, end, limit=limit)
            return json.dumps({"rows": data})

        if name == "query_form_performance":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            data = mock_data.form_performance(start, end)
            best = max(data, key=lambda r: r["conversion_rate_pct"])
            worst = min(data, key=lambda r: r["conversion_rate_pct"])
            return json.dumps({
                "rows": data,
                "best_form": best,
                "worst_form": worst,
            })

        if name == "get_data_coverage":
            return json.dumps({
                "data_start": mock_data.DATA_START.isoformat(),
                "data_end": mock_data.DATA_END.isoformat(),
                "source": "mock",
                "note": "Synthetic GA4-shaped data for demo. Swap in real GA4 by editing tools.py.",
            })

        return json.dumps({"error": f"Unknown tool: {name}"})

    except KeyError as e:
        return json.dumps({"error": f"Missing required argument: {e}"})
    except ValueError as e:
        return json.dumps({"error": f"Invalid argument: {e}"})
