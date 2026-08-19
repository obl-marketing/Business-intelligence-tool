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
            "Build the user-journey funnel with user counts, step-to-step conversion, and "
            "drop-off. By default it auto-detects a typical funnel (Session Start -> Product "
            "View -> Add to Cart -> Begin Checkout -> Add Payment Info -> Purchase), matching "
            "each stage to whatever the property ACTUALLY calls the event (it handles name "
            "variants like 'product view' vs 'view_item'). "
            "IMPORTANT: every site names events differently. If a stage comes back under "
            "'unmatched_stages', or the funnel looks wrong, do NOT report a step as 0 and "
            "blame the standard event name. Instead: read 'available_events' in the result "
            "(and the user's Training/knowledge notes) to find the real event names, then "
            "call this tool again with `funnel_events` set to the correct event names in "
            "order. If you still can't tell which event is which step, ASK the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date inclusive, YYYY-MM-DD"},
                "funnel_events": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: the property's real event names, in funnel order "
                        "(e.g. ['session_start','product view','add_to_cart','purchase']). "
                        "Use this to override the auto-detection with the actual events."
                    ),
                },
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
    {
        "name": "list_site_pages",
        "description": (
            "Discover the pages on the user's site from its sitemap, grouped by section, "
            "so you can audit ACROSS the whole website rather than one page. Use this "
            "first for any 'across my site' / 'all my pages' request (e.g. auditing "
            "popups, chatbot, and forms site-wide), then call audit_page on one "
            "representative page per key section. Needs SITE_BASE_URL set."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max sample URLs to return (default 150)."},
            },
        },
    },
    # ---------- GA4 Acquisition / Engagement (matches Reports, not event sums) ----------
    {
        "name": "query_traffic_summary",
        "description": (
            "**Source of truth for active users, sessions, page views, engagement rate, "
            "bounce rate, average session duration, AND the site-wide 'average engagement "
            "time per active user' (the headline number in GA4's UI).** Returns "
            "account-level totals that exactly match GA4 Reports > Acquisition/Engagement "
            "Overview. Use this for ANY question about 'how is my traffic doing', 'active "
            "users', 'sessions in May', 'engagement rate', 'bounce rate', and 'average "
            "engagement time per active user'. NOTE: avg_engagement_time_per_active_user "
            "(userEngagementDuration/activeUsers, e.g. ~33s) is DIFFERENT from and smaller "
            "than avg_session_duration (~75s) - the GA4 UI headline is the former; use it "
            "when the user says 'engagement time'. Set `country` to filter to one country "
            "(e.g. 'India'). Do NOT derive these numbers from query_events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "country": {
                    "type": "string",
                    "description": "Optional GA4 country name to filter to, e.g. 'India', 'United States'. Omit for all countries.",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_traffic_by_country",
        "description": (
            "Per-country traffic and engagement breakdown - active users, sessions, "
            "**average engagement time per active user**, engagement rate, and avg session "
            "duration for each country, matching GA4's Country dimension. Use for 'traffic "
            "by country', 'top countries', 'how is India doing', or to compare countries. "
            "For metrics about ONE country, you can also use query_traffic_summary with "
            "the `country` argument."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max countries to return (default 30)."},
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
            "is performing best/worst'. Set `country` to filter to one country - e.g. to "
            "answer 'Indian organic traffic', pass country='India' and read the Organic "
            "Search row."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "country": {
                    "type": "string",
                    "description": "Optional GA4 country name to filter to, e.g. 'India'.",
                },
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
                "country": {
                    "type": "string",
                    "description": "Optional GA4 country name to filter to, e.g. 'India'.",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    # ---------- GA4 custom-dimension slicing ----------
    {
        "name": "query_page_metrics",
        "description": (
            "Engagement and traffic metrics for ONE page or page group, matching GA4 "
            "Reports > Engagement > Pages. Returns active users, page views, sessions, "
            "**average engagement time per active user** (the GA4 'avg engagement time "
            "per user' metric = userEngagementDuration / activeUsers), engagement rate, "
            "bounce rate, and avg session duration - all scoped to the page. Use this "
            "for ANY question about how long users spend on a specific page, or that "
            "page's engagement/bounce. `page_path_contains` matches the URL path by "
            "substring (e.g. 'floor-tiles', '/wall-tiles/', '/products/'). Set "
            "`exact=true` to match ONE precise path instead of a substring - do this "
            "when the numbers must line up with a single row in the user's GA4 UI, "
            "since a substring match legitimately spans every sub-path containing "
            "the string and will show higher totals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "page_path_contains": {
                    "type": "string",
                    "description": "Page URL path to scope to, e.g. 'floor-tiles' (substring) or '/tiles/floor-tiles' (with exact=true)",
                },
                "exact": {
                    "type": "boolean",
                    "description": "If true, match this exact pagePath only (matches a single GA4 UI row). Default false = substring match.",
                },
            },
            "required": ["start_date", "end_date", "page_path_contains"],
        },
    },
    {
        "name": "query_pages_engagement_ranked",
        "description": (
            "Leaderboard of pages by engagement: per-page active users, page views, "
            "average engagement time per user, engagement rate, and bounce rate - "
            "matches GA4 Reports > Engagement > Pages and screens. Use for 'which pages "
            "do users spend most/least time on', 'most engaging pages', or to find "
            "high-traffic low-engagement pages worth fixing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "Max pages (default 25)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
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
        "name": "query_popup_breakdown",
        "description": (
            "Per-popup analytics: views, closes (rage-quits), submits, submit-rate "
            "and close-rate per (popup_id, page_path). Uses popup_view / popup_close "
            "/ popup_submit family of events with the customEvent:popup_id custom "
            "dimension. Use this for ANY question about popups - which popup converts "
            "best, which is being closed most, which page a popup performs best on, "
            "etc. Supports filtering to one popup_id or page substring. If popup_id "
            "is '(unknown)' for every row, surface the setup hint to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "popup_id": {"type": "string", "description": "Optional: filter to one popup_id"},
                "page_path_contains": {"type": "string", "description": "Optional: filter to pages containing this substring"},
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


# ---- QuickLook dealer-usage tools ----
# Five activity types, one shared parameter set. These count dealer activity
# from the QuickLook GetUsageStats API and roll it up by dealer / branch / zone
# using the dealer directory. All slicing is dealer-centric (no employee / BH /
# ZH reporting).

_QUICKLOOK_SHARED_PARAMS = {
    "start_date": {"type": "string", "description": "Start of the period, YYYY-MM-DD. Convert 'June' / 'last month' / 'last 3 months' to explicit dates using today's date."},
    "end_date": {"type": "string", "description": "End of the period, inclusive, YYYY-MM-DD."},
    "group_by": {
        "type": "string",
        "enum": ["dealer", "branch", "zone", "month"],
        "description": "How to roll up the counts. 'dealer' = one row per dealer (with branch & zone); 'branch' = totals per branch; 'zone' = totals per zone group (North/East/South/West); 'month' = totals per calendar month (a time series - use this for trends and for comparing features across months). Default 'dealer'.",
    },
    "zone": {"type": "string", "description": "Optional filter. Accepts a zone group ('North' matches North-1..North-4) or a specific zone ('East-1'). A trailing 'zone' word is fine ('North zone')."},
    "branch": {"type": "string", "description": "Optional filter to a single branch (e.g. 'N.EAST', 'ROWB')."},
    "dealer_code": {"type": "string", "description": "Optional filter to a single dealer's Merchant_Code / dealer code."},
    "min_count": {"type": "integer", "description": "Optional: only include dealers/branches/zones with AT LEAST this many activities (inclusive)."},
    "max_count": {"type": "integer", "description": "Optional: only include those with AT MOST this many (inclusive). For 'fewer than 5' pass max_count=4. By DEFAULT this counts only dealers who did at least 1 (so 'fewer than 5' returns dealers with 1-4) - dealers with ZERO activity are a separate 'inactive/never used' group and are NOT included unless include_zero=true."},
    "include_zero": {"type": "boolean", "description": "Default false. Set true ONLY when the user explicitly wants dealers with NO activity - 'inactive', 'never used', 'haven't used the app', 'zero sessions', 'dormant'. Then dealers with 0 are included; combine with max_count=0 to list exactly the dealers who did nothing. Do NOT set this for a plain 'fewer than N' question."},
    "include_non_dealers": {"type": "boolean", "description": "Default false. By default only REAL channel partners are counted: Merchant_Code = C+15 digits, in a real zone (North/South/East/West). Internal, test, developer, employee and customer accounts (e.g. CUSTILEKART, OBLTEST6, Employee/Dev Team zones) are excluded. Set true ONLY if the user explicitly asks to include those internal/test accounts."},
    "top": {"type": "integer", "description": "Max rows to return (default 100). The full matched total is reported separately."},
}

_QUICKLOOK_TOOL_DEFS = {
    "query_sessions": "Count app SESSIONS done by dealers (each row = one session). Use for 'how many sessions', 'which dealers logged in least', app adoption/engagement, and finding inactive dealers.",
    "query_design_activity": "Count DESIGNS shared by dealers (each row = one design/visualizer request). Use for 'how many designs shared' by dealer/branch/zone over a period.",
    "query_catalogue_activity": "Count CATALOGUES shared by dealers (each row = one catalogue activity). Use for 'how many catalogues shared' by dealer/branch/zone.",
    "query_quotation_activity": "Count QUOTATIONS generated/shared by dealers (each row = one quotation). Use for 'how many quotations' by dealer/branch/zone.",
    "query_voice_prompts": "Count VOICE PROMPTS done by dealers (each row = one voice search; samples include the transcript). Use for 'how many voice prompts' and to read what dealers searched by voice.",
}

for _name, _desc in _QUICKLOOK_TOOL_DEFS.items():
    TOOL_SCHEMAS.append({
        "name": _name,
        "description": (
            _desc + " Counts come from raw dealer-usage rows joined to the dealer "
            "directory; dealers outside the directory, internal/employee codes, and "
            "rows with no dealer code are reported separately as a coverage gap."
        ),
        "input_schema": {
            "type": "object",
            "properties": dict(_QUICKLOOK_SHARED_PARAMS),
            "required": ["start_date", "end_date"],
        },
    })


# ---- Tool dispatch ----

def _ga4_live() -> bool:
    import ga4_client
    return ga4_client.is_active()


def _meta_live() -> bool:
    import meta_client
    return meta_client.is_active()


_QUICKLOOK_TYPE_BY_TOOL = {
    "query_sessions": "sessions",
    "query_design_activity": "design",
    "query_catalogue_activity": "catalogue",
    "query_quotation_activity": "quotation",
    "query_voice_prompts": "voice_prompts",
}


def _meta_coverage() -> dict:
    if _meta_live():
        return {"source": "meta_live",
                "note": "Live Meta ad account. No demo date cutoff - query any real "
                        "date range (subject to the account's data retention)."}
    return {"source": "mock",
            "data_start": mock_data.DATA_START.isoformat(),
            "data_end": mock_data.DATA_END.isoformat()}


def _quicklook_coverage() -> dict:
    import quicklook_client
    import dealer_directory
    live = quicklook_client.is_active()
    dstats = dealer_directory.stats()
    cov = {
        "source": "quicklook_live" if live else "mock",
        "dealers_in_directory": dstats["dealers"],
        "zones": dstats["zone_groups"],
        "branches": dstats["branches"],
        "activity_types": ["design", "catalogue", "quotation", "sessions", "voice_prompts"],
        "dimensions": ["dealer", "branch", "zone"],
    }
    if not live:
        import mock_quicklook
        cov["data_start"] = mock_quicklook.MOCK_START.isoformat()
        cov["data_end"] = mock_quicklook.MOCK_END.isoformat()
        cov["note"] = ("Demo dealer-usage data until QUICKLOOK_API_TOKEN is set. "
                       "Counts are computed from raw rows joined to the dealer directory.")
    return cov


def _run_quicklook_tool(name: str, args: dict[str, Any]) -> str:
    import quicklook_client
    import quicklook_analytics

    type_key = _QUICKLOOK_TYPE_BY_TOOL[name]
    start = _parse_date(args["start_date"])
    end = _parse_date(args["end_date"])
    period = {"start": args["start_date"], "end": args["end_date"]}

    if quicklook_client.is_active():
        fetched = quicklook_client.fetch_rows(type_key, args["start_date"], args["end_date"])
        rows, source, truncated = fetched["rows"], "quicklook_live", fetched["truncated"]
    else:
        import mock_quicklook
        rows, source, truncated = mock_quicklook.rows(type_key, start, end), "mock", False

    def _int(key):
        v = args.get(key)
        return int(v) if v is not None else None

    result = quicklook_analytics.summarize(
        type_key, rows,
        group_by=args.get("group_by", "dealer"),
        zone=args.get("zone"), branch=args.get("branch"),
        dealer_code=args.get("dealer_code"),
        min_count=_int("min_count"), max_count=_int("max_count"),
        include_zero=bool(args.get("include_zero", False)),
        dealers_only=not bool(args.get("include_non_dealers", False)),
        top=int(args.get("top", 100)),
        source=source, truncated=truncated, period=period,
    )
    return json.dumps(result)


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
            extra = {}
            if _ga4_live():
                res = ga4_client.user_journey_funnel(
                    args["start_date"], args["end_date"],
                    funnel_events=args.get("funnel_events"),
                )
                data = res.get("funnel", [])
                # carry the adaptivity diagnostics through to the agent
                for k in ("unmatched_stages", "note", "available_events"):
                    if res.get(k):
                        extra[k] = res[k]
            else:
                data = mock_data.user_journey_funnel(start, end)
            if not data:
                return json.dumps({
                    "funnel": [],
                    "note": extra.get("note",
                            "No funnel events matched. The property may use different event "
                            "names - check available_events and the user's Training notes, "
                            "then retry with funnel_events, or ask the user."),
                    "available_events": extra.get("available_events"),
                })
            biggest_drop = max(data[1:], key=lambda s: s["drop_off_from_previous_pct"]) if len(data) > 1 else data[0]
            out = {
                "funnel": data,
                "biggest_drop_off": {
                    "between_steps": f"{biggest_drop['step']-1} -> {biggest_drop['step']}",
                    "step_name": biggest_drop["name"],
                    "drop_off_pct": biggest_drop["drop_off_from_previous_pct"],
                },
                "source": "ga4_live" if _ga4_live() else "mock",
            }
            out.update(extra)
            return json.dumps(out)

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
                    "meta_ads": _meta_coverage(),
                    "quicklook_dealer_usage": _quicklook_coverage(),
                })
            return json.dumps({
                "data_start": mock_data.DATA_START.isoformat(),
                "data_end": mock_data.DATA_END.isoformat(),
                "source": "mock",
                "connected_sources": ["google_analytics", "google_ads", "meta_ads",
                                      "quicklook_dealer_usage"],
                "meta_ads": _meta_coverage(),
                "quicklook_dealer_usage": _quicklook_coverage(),
                "note": "GA4 / Google Ads / Meta Ads are synthetic demo data. "
                        "QuickLook dealer usage is live when a token is set (see its block).",
            })

        # ---------- QuickLook dealer usage ----------
        if name in _QUICKLOOK_TYPE_BY_TOOL:
            return _run_quicklook_tool(name, args)

        # ---------- Frontend audit ----------
        if name == "audit_page":
            import frontend_audit
            data = frontend_audit.audit_page(args["url_or_path"])
            return json.dumps(data)

        if name == "list_site_pages":
            import frontend_audit
            data = frontend_audit.list_site_pages(limit=int(args.get("limit", 150)))
            return json.dumps(data)

        # ---------- GA4 Reports-aligned (acquisition / engagement) ----------
        if name == "query_traffic_summary":
            if not _ga4_live():
                return json.dumps({
                    "note": "Traffic summary needs the live GA4 connection. "
                            "In demo mode, use query_pageviews instead."
                })
            return json.dumps(ga4_client.traffic_summary(
                args["start_date"], args["end_date"], country=args.get("country")))

        if name == "query_traffic_by_country":
            if not _ga4_live():
                return json.dumps({"note": "Per-country traffic needs the live GA4 connection."})
            data = ga4_client.traffic_by_country(
                args["start_date"], args["end_date"], limit=int(args.get("limit", 30)))
            return json.dumps({"rows": data, "countries": len(data), "source": "ga4_live"})

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
            data = ga4_client.acquisition_by_channel(
                args["start_date"], args["end_date"], country=args.get("country"))
            return json.dumps({"rows": data, "channels": len(data),
                               "country": args.get("country") or "all countries",
                               "source": "ga4_live"})

        if name == "query_acquisition_by_source_medium":
            if not _ga4_live():
                return json.dumps({"note": "Acquisition-by-source needs the live GA4 connection."})
            limit = int(args.get("limit", 25))
            data = ga4_client.acquisition_by_source_medium(
                args["start_date"], args["end_date"], limit=limit, country=args.get("country"))
            return json.dumps({"rows": data, "sources": len(data),
                               "country": args.get("country") or "all countries",
                               "source": "ga4_live"})

        # ---------- GA4 per-page metrics ----------
        if name == "query_page_metrics":
            if not _ga4_live():
                return json.dumps({"note": "Per-page metrics need the live GA4 connection."})
            return json.dumps(ga4_client.page_metrics(
                args["start_date"], args["end_date"], args["page_path_contains"],
                exact=bool(args.get("exact", False)),
            ))

        if name == "query_pages_engagement_ranked":
            if not _ga4_live():
                return json.dumps({"note": "Page engagement leaderboard needs the live GA4 connection."})
            data = ga4_client.pages_engagement_ranked(
                args["start_date"], args["end_date"], limit=int(args.get("limit", 25))
            )
            return json.dumps({"rows": data, "count": len(data), "source": "ga4_live"})

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

        if name == "query_popup_breakdown":
            if not _ga4_live():
                return json.dumps({"note": "Popup breakdown needs the live GA4 connection."})
            data = ga4_client.popup_breakdown(
                args["start_date"], args["end_date"],
                popup_id=args.get("popup_id"),
                page_path_contains=args.get("page_path_contains"),
            )
            data["source"] = "ga4_live"
            return json.dumps(data)

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
            if _meta_live():
                import meta_client
                data = meta_client.meta_ads_summary(args["start_date"], args["end_date"])
                data["source"] = "meta_live"
                return json.dumps(data)
            return json.dumps(mock_data.meta_ads_summary(start, end))

        if name == "query_meta_ads_campaigns":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _meta_live():
                import meta_client
                data = meta_client.meta_ads_campaigns(args["start_date"], args["end_date"])
                source = "meta_live"
            else:
                data = mock_data.meta_ads_campaigns(start, end)
                source = "mock"
            spending = [c for c in data if c["spend"] > 0]
            best = max(spending, key=lambda c: c["roas"]) if spending else None
            worst = min(spending, key=lambda c: c["roas"]) if spending else None
            return json.dumps({"rows": data, "best_roas": best, "worst_roas": worst, "source": source})

        if name == "query_meta_ads_creatives":
            start = _parse_date(args["start_date"])
            end = _parse_date(args["end_date"])
            if _meta_live():
                import meta_client
                data = meta_client.meta_ads_creatives(args["start_date"], args["end_date"])
                source = "meta_live"
            else:
                data = mock_data.meta_ads_creatives(start, end)
                source = "mock"
            best = data[0] if data else None
            worst = data[-1] if data else None
            return json.dumps({"rows": data, "best_creative": best, "worst_creative": worst, "source": source})

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
