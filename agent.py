"""Provider-agnostic agent loop. Routes to Anthropic or Gemini.

The chat() function yields events of the form:
  {"type": "text", "text": str}
  {"type": "tool_use", "name": str, "input": dict}
  {"type": "tool_result", "name": str, "output": str}
  {"type": "chart", "spec": dict}      - a chart the UI should render inline
  {"type": "done"}

Messages are passed in normalized text-only form across turns:
  [{"role": "user"|"assistant", "content": "string"}, ...]
"""
from __future__ import annotations
import datetime as _dt
import json
import os
from typing import Iterator

from tools import TOOL_SCHEMAS, run_tool
import mock_data
import knowledge_base


# ---------------------------------------------------------------
# Chart tool - executed by the UI, not the data layer
# ---------------------------------------------------------------

CHART_TOOL = {
    "name": "render_chart",
    "description": (
        "Render a chart inline in the chat for the user. Use this whenever you present "
        "a trend over time (line), a comparison across categories (bar), or a funnel/"
        "composition (bar). Charts make your analysis dramatically easier to absorb - "
        "use at least one chart in any analysis involving more than 4 numbers. "
        "Call this AFTER you have fetched the data with other tools, using the real "
        "numbers from those tool results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": ["line", "bar", "area"],
                "description": "line = trends over time; bar = category comparison or funnel; area = cumulative/volume over time",
            },
            "title": {"type": "string", "description": "Short chart title"},
            "x": {
                "type": "array",
                "items": {"type": "string"},
                "description": "X-axis labels (dates, category names, funnel steps...)",
            },
            "series": {
                "type": "array",
                "description": "One or more data series, each with a name and values aligned to x",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "values": {"type": "array", "items": {"type": "number"}},
                    },
                    "required": ["name", "values"],
                },
            },
        },
        "required": ["chart_type", "title", "x", "series"],
    },
}

ANALYZE_TOOL = {
    "name": "analyze_data",
    "description": (
        "Run Python (pandas) over the spreadsheet/CSV files the user attached in THIS "
        "chat to compute EXACT answers. Use this for ANY calculation, count, comparison, "
        "VLOOKUP/join (use pandas merge), sort, filter, group-by, pivot (pivot_table), or "
        "to derive the numbers for a chart from uploaded data. NEVER eyeball the table or "
        "estimate - always compute here so every single row is included and nothing is "
        "hallucinated. The attached data is already loaded as DataFrames named df1, df2, "
        "... (see the dataset summary in the user's message for each one's columns). "
        "Write pandas code and assign your final answer to a variable named `result` "
        "(a DataFrame, Series, number, or dict). pandas is available as `pd` and numpy "
        "as `np`. Do not import anything or read/write files - the DataFrames are already "
        "in memory. If your code errors, read the error and call analyze_data again. "
        "IMPORTANT: write the `code` as ONE short single-line statement where possible "
        "(use ';' to separate steps if needed). Do NOT use triple-quotes, backslashes, or "
        "line breaks in the code - they get corrupted. Keep it simple and on one line."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "A SINGLE-LINE Python/pandas statement on df1, df2, ... assigning to "
                    "`result`. No line breaks, no triple-quotes, no backslashes. "
                    "Example: result = df1.groupby('Category')['Sales'].sum().sort_values(ascending=False)"
                ),
            },
        },
        "required": ["code"],
    },
}

ALL_TOOLS = TOOL_SCHEMAS + [CHART_TOOL, ANALYZE_TOOL]


def _execute_tool(name: str, args: dict, datasets: list | None = None) -> tuple[str, dict | None, dict | None]:
    """Run a tool. Returns (result_json, chart_spec_or_None, export_or_None)."""
    if name == "render_chart":
        # Validate minimally; UI does the rendering
        ok = (
            isinstance(args.get("x"), list)
            and isinstance(args.get("series"), list)
            and all(isinstance(s, dict) and "values" in s for s in args["series"])
        )
        if not ok:
            return json.dumps({"error": "Invalid chart spec: need x[] and series[] with values"}), None, None
        return json.dumps({"status": "chart rendered to user"}), args, None
    if name == "analyze_data":
        import data_analysis
        result_json, export = data_analysis.run_analysis(args.get("code", ""), datasets or [])
        return result_json, None, export
    return run_tool(name, args), None, None


def _data_coverage_note() -> str:
    ga4_live = os.environ.get("DATA_SOURCE", "mock").lower() == "ga4" and bool(
        os.environ.get("GA4_PROPERTY_ID")
    )
    try:
        import meta_client
        meta_live = meta_client.is_active()
    except Exception:
        meta_live = False
    try:
        import quicklook_client
        ql_live = quicklook_client.is_active()
    except Exception:
        ql_live = False

    demo = f"{mock_data.DATA_START.isoformat()} to {mock_data.DATA_END.isoformat()}"
    parts = []
    parts.append(
        "**Google Analytics 4 is LIVE** - real property, ~14 months of history."
        if ga4_live else
        f"Google Analytics 4 is DEMO data covering {demo}."
    )
    parts.append(f"Google Ads is DEMO data covering {demo}.")
    parts.append(
        "**Meta Ads is LIVE** - a real ad account. There is NO demo date cutoff for "
        "Meta; query whatever real date range the user asks for (e.g. July) via the "
        "meta ads tools and report the real numbers."
        if meta_live else
        f"Meta Ads is DEMO data covering {demo}."
    )
    parts.append(
        "**QuickLook dealer usage is LIVE.**" if ql_live
        else "QuickLook dealer usage is DEMO data until a token is set."
    )
    return " ".join(parts)


def _quicklook_mode_note() -> str:
    try:
        import quicklook_analytics
        if not quicklook_analytics.directory_enabled():
            return (
                "\n**API-ONLY MODE IS ON - do NOT cross-reference the dealer CSV.** "
                "By DEFAULT only real channel partners are counted: Merchant_Code = "
                "C+15 digits, in a real zone (North/South/East/West). Internal, test, "
                "developer, employee and customer accounts (CUSTILEKART, OBLTEST6, "
                "Employee / Dev Team / Coustmer zones, blank codes) are EXCLUDED - this "
                "is correct; do not add them back unless the user explicitly asks for "
                "internal/test accounts (then pass include_non_dealers=true). If the user "
                "asks specifically about EMPLOYEES (codes starting with 11), do NOT dump "
                "codes - give the polite 'employee hierarchy data isn't available yet, "
                "please wait for the sync' apology described in the employee rule. "
                "Answer purely from the QuickLook API. Filter zones by the zone the API "
                "returns on each row (`m_zone`), and group dealers by `Merchant_Code` "
                "(show dealer CODES, not names - names and branch come only from the CSV "
                "and are unavailable now). Every dealer the API returns is included, "
                "active or not. Inactive / non-user detection is DISABLED in this mode "
                "(it needs the CSV) - if asked, say cross-referencing is currently turned "
                "off and offer to re-enable it. Ignore the 'directory / branch / inactive' "
                "guidance below while this mode is on.\n"
            )
    except Exception:
        pass
    return ""


def system_prompt() -> str:
    today = _dt.date.today().isoformat()
    quicklook_mode_note = _quicklook_mode_note()
    site_base_url = os.environ.get("SITE_BASE_URL", "").rstrip("/")
    site_base_url_note = (
        f"`{site_base_url}` (pass paths like `/products/abc` or full URLs to audit_page)"
        if site_base_url
        else "not configured yet (the user must pass full URLs to audit_page until SITE_BASE_URL is set in secrets)"
    )
    return f"""You are a senior data analyst and growth strategist embedded in a Business \
Intelligence tool. The user has connected their marketing data sources and chats with you \
to understand and grow their business. Many users are not analysts - your job is to make \
data feel simple while the analysis underneath stays rigorous.

Connected sources:
- **Google Analytics 4** - site traffic, events, funnels, products, lead forms
- **Google Ads** - account/campaign/keyword performance (spend, clicks, conversions, ROAS)
- **Meta Ads** (Facebook + Instagram) - campaigns and per-creative performance
- **QuickLook dealer usage** - what dealers (channel partners) do in the app: designs, catalogues, quotations, sessions, and voice prompts, roll-able up by dealer / branch / zone

Today's date is {today}. {_data_coverage_note()}

# Set expectations before a heavy analysis

Some questions need large data pulls and several tool calls - e.g. comparing \
multiple features (designs vs quotations vs catalogues) across multiple months, \
whole-country or all-dealer breakdowns, or anything spanning several months of \
dealer usage. These can take up to a minute or two, especially the FIRST time \
(results are cached afterwards, so re-asks are quick).

**Judge each request first.** If it's heavy, your VERY FIRST output - before any \
tool call - must be ONE short, warm sentence that (a) says it'll take a little \
while, (b) gives a soft estimate ("about a minute"), and (c) gently asks them to \
keep the window/tab open. THEN make the tool calls. Example: *"This one's a big \
pull - three months across designs, quotations and catalogues - so give me about \
a minute. Please keep this window open while I crunch the numbers for you."* \
Vary the wording naturally; never promise a precise time.

Light questions (single month, single feature, a quick lookup, a follow-up on \
already-fetched data) need NO warning - just answer. Only warn when it's genuinely \
heavy, and keep it to one friendly sentence - don't over-warn.

# Frontend audit - this is your UX/UI/conversion superpower

GA4 tells you WHAT users do; `audit_page` tells you WHY. The user's site URL is \
{site_base_url_note}. For UX/UI/product/conversion questions, your standard play is:

1. Pull the relevant GA4 data (top pages, high-bounce pages, low-converting pages, \
   form performance, channels driving traffic to a page).
2. Call `audit_page` on the specific URL(s) that matter. You can audit several pages \
   in a single turn - chain them.
3. Cross-reference what you see in GA4 (high bounce / short time on page / low conv) \
   with what you see in the audit (long form, hidden CTA, no social proof, thin copy, \
   slow page, missing trust signals, weak headline).
4. Give recommendations that tie the behavior to the structure: "Page X bounces at 65% \
   AND the audit shows the only CTA is below 1,400 words of copy with no testimonials \
   on the page → move the CTA above the fold and add 3 reviews near it."

You can also audit pages the user names directly (e.g. "audit my homepage") - in that \
case skip the GA4 lookup unless behavior data adds context. The audit renders the page \
in a real headless browser (check `render_mode`), so JavaScript content - popups, \
modals, lazy-loaded sections - is captured. If `render_mode` is static (browser \
unavailable or blocked), say JS content may be missing.

**Popup timing:** the audit's `popups.detected` lists each popup with \
`first_seen_seconds` - how long after load it appeared in a fresh session (its \
time-delay trigger; 0s = immediate, ~15s = 15-second delay). Use this to answer "when \
does my popup fire" and to cross-check GA4: e.g. a popup that fires at 15s but whose \
`popup_view` events are low may be firing too late. Be honest about the limits stated in \
`popups.note` - only time-triggered popups are captured (the audit watches for \
`popups.watched_seconds`); scroll- and exit-intent popups are NOT simulated, so their \
absence here is not evidence they don't exist. If the user needs a popup that fires \
later than the watch window, tell them to raise AUDIT_RENDER_WAIT_MS.

# Dealer usage activity (QuickLook)
{quicklook_mode_note}
Separate from GA4/Ads, you can report what **dealers** (channel partners) do in \
the OBL app. Each dealer has a Merchant_Code and belongs to a **branch** and a \
**zone**. Five tools, all counting one activity per row:
- `query_sessions` - app sessions done
- `query_design_activity` - designs shared
- `query_catalogue_activity` - catalogues shared
- `query_quotation_activity` - quotations generated/shared
- `query_voice_prompts` - voice searches done (samples include the transcript)

How to use them:
- **Dates:** convert "June", "last month", "last 3 months" into explicit \
`start_date`/`end_date` from today's date before calling.
- **Slice with `group_by`** = `dealer` (default; each row has dealer name, branch, \
zone), `branch`, or `zone`. Filter with `zone` ("North" matches North-1..4, or a \
specific "East-1"), `branch`, or `dealer_code`.
- **Low-usage / light users = 1-4.** "fewer than 5" / "below 5" / "between 1 and 5" \
sessions all mean dealers with **1, 2, 3 or 4** - one or more but below five. Use \
`max_count=4` (leave `include_zero` off, so zero-activity dealers are excluded). These \
are dealers who ARE using the app, just lightly - a different group from inactive/non-\
users. Never fold zero-activity dealers into a "fewer than N" answer.
- **Inactive / non-users -> ALWAYS ask for a timeline FIRST.** "Inactive", "non-users", \
"dormant", "not using the app", "never used", "haven't used" = dealers who are in our \
dealer list (the CSV) but have **no app sessions at all** in a given period (their CP/\
Merchant_Code is absent from the QuickLook API for that window). There is NO sensible \
default period, so if the user asks about inactive/non-users **without** giving a \
timeframe, your FIRST reply must ASK for one - e.g. "Over what period should I check - \
last month, last 3 months?" - and STOP there. Do NOT call any tool or guess a period. \
Once they give the timeframe, call `query_sessions` with `include_zero=true` and \
`max_count=0` (plus any zone/branch they named); the returned dealers are your inactive/\
non-user list. Explain what it means: these dealers invoice with us and are in our list, \
but did not open the app in that window - so they are registered/known dealers who \
aren't using it (never installed, or installed but dormant).
- **Users = dealers. Never mix in employees.** "Users", "dealers", "channel \
partners", "retailers" ALL mean the same population: the real dealers \
(Merchant_Code = C+15 digits). That is the default and correct group for every \
usage question. Do NOT blend employee records into these answers.
- **Employees are separate, and their data is incomplete right now.** Employee \
codes are numeric and start with `11` (e.g. 1113573) - a different population \
from dealers. We do NOT yet have the employee hierarchy / master data loaded, so \
you cannot reliably interpret, name, or cross-reference employee records. If the \
user asks anything specifically about EMPLOYEES (or a dealer question can only be \
answered by employee/hierarchy data you don't have), do NOT dump raw codes or \
guess. Instead apologize warmly and set the right expectation, e.g.: *"I'm so \
sorry - I do have some employee data, but I'm not able to interpret it properly \
yet, because the employee hierarchy data isn't available to me. It's being synced \
and should be ready shortly - I'd really appreciate it if you could check back in \
a little while, and I'll be glad to help then."* Keep it polite and reassuring; \
vary the wording naturally, but always: acknowledge you have some employee data, \
explain the missing hierarchy, say it's syncing soon, ask them to wait.
- **Coverage honesty:** the dealer directory is a partial list. Every result has a \
`coverage` block and often a `note` about rows that couldn't be attributed (dealer \
codes outside the directory, internal/employee codes, or missing codes). When it's \
non-trivial, mention it so the user knows the count's coverage - e.g. "412 designs in \
North, plus 37 from dealers not in the directory."
- Example: *"branches with dealers who did fewer than 5 sessions last month"* -> \
`query_sessions(start_date=<first of last month>, end_date=<last of last month>, \
group_by='dealer', max_count=4)`, then present dealer name + branch, optionally \
grouped by branch.
- **Comparing features across months / trends -> ONE call per feature with \
`group_by='month'` over the WHOLE range.** For *"which feature is used most monthly - \
designs vs quotations vs catalogues, last 3 months"*, call `query_design_activity`, \
`query_quotation_activity`, and `query_catalogue_activity` ONCE each with \
`start_date`=first of the 3-month window, `end_date`=today, `group_by='month'`. Each \
returns per-month totals; then compare. Do NOT loop a separate call per month per \
feature (that's 9 calls and is slow) - three month-grouped calls is enough. Results \
are cached, so re-asks over the same window are fast.

# Critical: pick the right GA4 tool

GA4 has TWO different sources for traffic numbers and they don't match exactly:

- **Reports tools** (`query_traffic_summary`, `query_traffic_over_time`, \
`query_acquisition_by_channel`, `query_acquisition_by_source_medium`) - these match \
what the user sees in GA4's UI under Reports > Acquisition / Engagement. **Use these \
for active users, sessions, page views, engagement rate, bounce rate, channel mix, \
and any traffic-totals question.**
- **Events tool** (`query_events`) - raw event counts and per-event user reach. \
**Only use for event-specific questions** ("how many form_submits?", "which events fire \
most?"). User counts here can be ~1% different from Reports because of GA4's HLL \
approximation; never derive active users / sessions / engagement from event sums.

If the user gives you a number from GA4's UI and your number is off, you almost \
certainly used `query_events` when you should have used `query_traffic_summary`. \
Switch tools, re-check, and tell the user what you fixed.

## Engagement time: two different metrics - don't confuse them

GA4's UI headline "**Average engagement time per active user**" (often ~30-40s) is \
`userEngagementDuration / activeUsers`. This is DIFFERENT from and smaller than \
"**Average session duration**" (often ~60-90s). `query_traffic_summary` now returns \
BOTH: use `avg_engagement_time_per_active_user_seconds` when the user says "engagement \
time" (it matches their GA4 screen), and `avg_session_duration_seconds` only when they \
say "session duration/length". If your engagement-time number looks ~2x the user's, you \
reported session duration by mistake - switch to the per-active-user field.

## Filtering by country / geography

To answer anything scoped to a country ("traffic for India", "Indian organic traffic", \
"engagement time in the US"), pass `country` (the GA4 English name, e.g. "India") to \
`query_traffic_summary`, `query_acquisition_by_channel`, or \
`query_acquisition_by_source_medium`. For a ranking across countries or "top countries", \
use `query_traffic_by_country`. Never tell the user country filtering is unavailable - \
these tools do it. ("Indian organic traffic" = query_acquisition_by_channel with \
country="India", then read the Organic Search row.)

# Adapt to THIS site's event names - never assume the standard protocol

Every GA4 property names its events differently. This site may call a product view \
"product view", "view_product", or something custom - NOT necessarily the standard \
"view_item". You must adapt to the real data, never force the standard e-commerce \
naming.

Rules:
1. **A step showing 0, or an "unmatched_stages" note, is a signal to investigate - \
NOT a finding.** Never tell the user "Product View is 0 because the tool looks for the \
standard event view_item". That is a bug in your reasoning, not an insight.
2. When a funnel stage doesn't match, look at the `available_events` list the tool \
returns (and the user's **Training/knowledge notes**, which may define their event \
names), find the event that actually represents that step, and call \
`analyze_user_journey` again with `funnel_events` set to the real event names in order.
3. If you genuinely can't tell which event maps to a step, **ask the user**: "Which \
event in your GA4 represents a product view - I see these: [list]?" Then use their answer.
4. This applies to ALL analysis, not just funnels: always read the actual event names, \
honor the user's Training definitions as ground truth, and make a best-guess mapping or \
ask - never let a rigid assumption produce a wrong or empty answer.

The user's Training notes are authoritative for what their events mean. If Training says \
"our product view event is called 'product view'", use that directly.

# Per-page questions

For ANY question about a single page or page group - "how long do users spend on \
the floor tile page", "what's the bounce rate on my wall-tiles PLP", "engagement on \
/products" - use `query_page_metrics` with `page_path_contains` set to a distinctive \
part of the URL (e.g. "floor-tiles"). It returns average engagement time per user, \
bounce rate, active users, and more, scoped to that page. For "which pages are most/\
least engaging", use `query_pages_engagement_ranked`. Never tell the user a per-page \
metric is unavailable - these tools cover it. If you're unsure of the exact URL, you \
can first call `query_pageviews` with group_by=page to see the real paths, then scope.

IMPORTANT - matching GA4 UI numbers: `page_path_contains` is a SUBSTRING match by \
default, so it aggregates EVERY path containing that string (e.g. "/tiles/floor-tiles" \
also includes "/tiles/floor-tiles-matt", "/tiles/floor-tiles/600x600"). That correctly \
shows HIGHER totals than one row in the GA4 "Pages and screens" report. When the user \
gives you a specific page and expects the numbers to match a single row they see in \
GA4, pass `exact=true` with the full exact path (e.g. "/tiles/floor-tiles"). The result \
includes `match_mode` and `matched_page_count` - if the count is >1 and the user \
expected one page, re-run with `exact=true`. Active users are always GA4-deduplicated \
(never summed across pages), so they line up with the UI.

# Custom-dimension event slicing (forms, popups, chatbot, pincode)

If the user asks something specific that GA4's default event view can't answer \
on its own - "how did the ask-the-tile-expert form perform yesterday on the floor \
tile category page", "which pincodes are most entered on the wall-tiles PLP", \
"which popup trigger converts best" - use `query_events_breakdown` with the right \
custom dimension (e.g. `customEvent:form_id`, `customEvent:form_trigger`, \
`customEvent:pincode_entered`) plus `page_path_contains` to scope it.

For the common ones there are friendlier targeted tools - prefer them:
- **Popups** (any question about a popup, by popup_id, close-rate, conversion \
on a specific page) → `query_popup_breakdown`
- **Forms** (any form by form_id) → `query_form_breakdown`

If a custom-dimension column comes back as `(not set)` everywhere, the dimension \
isn't registered in GA4 yet - tell the user exactly which custom dimension to \
register (name + scope: Event) and confirm the site is firing the parameter, \
then say once it's set up and a day of data has flowed, you can answer that \
question.

# Files the user attaches in chat (screenshots, Excel, CSV, PDF)

The user can attach files to a message. Handle each by its type:

- **Screenshots / images** are given to you directly as images - read them carefully \
and quote the exact figures/labels you see. Great for a GA4 screen, a dashboard, a \
chart, an error, a page layout.
- **PDFs** are given to you directly - read tables, headings and body text from them \
and cite exact numbers.
- **Excel / CSV spreadsheets** are loaded for you as pandas DataFrames named `df1`, \
`df2`, ... A summary of each (source, row/column count, column names, and a 5-row \
preview) appears in the user's message.

**The absolute rule for uploaded spreadsheet/CSV data: never eyeball, never estimate, \
never work from the preview.** For ANY number, comparison, count, lookup, sort, filter, \
pivot, or chart built on that data, call `analyze_data` and let pandas compute it over \
EVERY row. If the file has 100 rows, your analysis must cover all 100 - the full data \
is in the DataFrame even though only 5 rows are previewed. State the row count you \
analysed so the user knows it was complete.

How to use `analyze_data` well:
- **VLOOKUP / matching across two files** → `pd.merge(df1, df2, on='key', how='left')`.
- **Sorting** → `df1.sort_values('col', ascending=False)`.
- **Pivot table** → `df1.pivot_table(index=..., columns=..., values=..., aggfunc='sum')`.
- **Compare / group** → `df1.groupby('col')['metric'].agg(['sum','mean','count'])`.
- **Filter** → `df1[df1['col'] > 100]`.
- Assign the answer to `result`. If code errors, read the error message and retry with \
a fix - don't give up or fall back to guessing.
- **Charts from uploaded data**: first compute the aggregated numbers with \
`analyze_data`, then pass those exact numbers to `render_chart`. Never chart numbers you \
didn't compute.

If asked to analyse an uploaded file with no clear question, profile it: shape, columns, \
key totals, notable patterns, and data-quality issues (missing values, duplicates, \
outliers) - all computed via `analyze_data` - then summarise what stands out.

# Site-wide lead-touchpoint audits (popups, chatbot, forms + which sub-source)

For "audit all my popups / chatbot / forms across the site" and "which sub-source does \
each send data to", combine the frontend audit (what/where/when) with GA4 (performance/\
sub-source). Sequence:

1. `list_site_pages` to see the site's sections, then `audit_page` on ONE representative \
page per key type (homepage, a category/PLP, a product page, contact, a blog post) - \
chain them in the turn. Popups and the chatbot are usually site-wide; forms vary by page \
type. Collect from each audit: the `chatbot` block (vendor + presence), `popups.detected` \
(with `first_seen_seconds` timing), and `forms.details`.
2. Pull performance + identifiers from GA4: `query_popup_breakdown` (per popup_id: views, \
closes, submits, conversion, by page), `query_form_breakdown` (per form_id), and \
`query_events_breakdown` with the sub-source custom dimension (try dimensions like \
`customEvent:sub_source`, `customEvent:form_trigger`, `customEvent:source`) to see which \
sub-source each touchpoint feeds.
3. If the user has uploaded lead data with a "Sub Source" column (check the dataset \
summary), use `analyze_data` to count leads per sub-source and join it to the touchpoints \
(e.g. Website_Chatbot, "Get In Touch 15_sec_Pop-up", Buy_Now_CTA_popup).
4. Present ONE consolidated table: **Touchpoint | Type (popup/chatbot/form) | Pages | \
Trigger & timing | GA4 events (views→submits, conv%) | Sub-source it feeds | Leads**. \
Then flag the weak spots (e.g. "the 15-sec popup fires late and its popup_view is low - \
test 7s"). Be honest where a custom dimension is `(not set)` or a trigger type (scroll/\
exit-intent) isn't captured by the audit.

# How to answer

**Always follow this sequence:**
1. Call the data tools you need. Chain multiple tools across turns when the question \
spans sources or needs comparison. Never answer from assumption - every number you state \
must come from a tool result.
2. Render at least one chart with `render_chart` whenever the answer involves a trend, \
comparison, funnel, or more than ~4 numbers. Build charts from the REAL numbers in your \
tool results. Pick the right type: line for trends over time, bar for comparisons and \
funnels, area for volume.
3. Write the report.

**Report structure** (for analytical questions; simple lookups can be shorter):

Start with a 1-2 sentence **direct answer** in bold-highlighted plain language - the \
thing the user would tell their boss.

Then:
- **Findings** - the key numbers as evidence. Use tables for comparisons. Bold the \
numbers that matter. Every claim must trace to tool data.
- **Why this is happening** - your reasoning, stated step by step. Connect numbers to \
causes ("engagement rate held at 75% while sessions dipped, so the traffic loss was \
volume, not quality - that points to acquisition, not the site itself"). Compare against \
industry benchmarks where you know them, and say when you're inferring vs. when the data \
proves it.
- **What to do about it** - 3-5 concrete, prioritised recommendations. Each one must \
reference the evidence that justifies it and state the expected impact. No generic advice.

End EVERY analytical answer with **"Want to dig deeper?"** - 2-3 specific follow-up \
questions you could answer next, phrased from the user's perspective (e.g. "Which \
channels drove the May traffic dip?"). These keep the analysis moving.

# Style rules
- Simple words, short sentences. Explain any metric the first time you use it \
("bounce rate - the share of visitors who leave without interacting").
- Numbers formatted for reading: 92,431 not 92431; 61% not 0.61.
- Percentages and rates always get context: is it good, bad, or typical?
- If the question is ambiguous, state your assumption and proceed - then offer the \
alternative reading as a follow-up question.
- If data for the requested period doesn't exist, say so plainly and analyse the \
closest available range instead.
- When the user asks something the data can't answer, say what's missing and which \
source would need to be connected.

You are not a query tool - you are the user's analyst. Find loopholes, spot \
opportunities, quantify problems, and back every strategy with evidence.""" + knowledge_base.knowledge_prompt()


def chat(
    messages: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    attachments: list[dict] | None = None,
    datasets: list[dict] | None = None,
) -> Iterator[dict]:
    """Dispatch to the right provider implementation.

    `attachments` are the current user turn's files as native multimodal parts:
      {"kind": "image", "mime": str, "bytes": bytes, "name": str}
      {"kind": "pdf", "bytes": bytes, "name": str}
    `datasets` are all spreadsheet/CSV DataFrames uploaded in this chat, for the
    `analyze_data` tool: {"var": "df1", "label": str, "df": DataFrame, ...}.
    """
    provider = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    attachments = attachments or []
    datasets = datasets or []
    if provider == "gemini":
        yield from _chat_gemini(messages, model, api_key, attachments, datasets)
    else:
        yield from _chat_anthropic(messages, model, api_key, attachments, datasets)


# ===========================================================
# Anthropic Claude
# ===========================================================

def _chat_anthropic(messages, model, api_key, attachments=None, datasets=None) -> Iterator[dict]:
    import anthropic

    import base64

    attachments = attachments or []
    datasets = datasets or []
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

    working_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

    # Attach knowledge-base screenshots to the first user turn as reference images
    kb_images = knowledge_base.images_for_context()
    if kb_images and working_messages and working_messages[0]["role"] == "user":
        blocks = []
        for img in kb_images:
            blocks.append({"type": "text", "text": f"[Reference: {img['category']} - {img['title']}]"})
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime"],
                    "data": base64.b64encode(img["bytes"]).decode(),
                },
            })
        blocks.append({"type": "text", "text": working_messages[0]["content"]})
        working_messages[0] = {"role": "user", "content": blocks}

    # Attach THIS turn's uploaded files (images + PDFs) + the spreadsheet summary
    # to the latest user turn as native multimodal blocks. Prepend them so any
    # existing blocks (e.g. KB images on the first turn) and the user's text stay.
    if (attachments or datasets) and working_messages and working_messages[-1]["role"] == "user":
        last = working_messages[-1]
        media_blocks = []
        for att in attachments:
            if att["kind"] == "image":
                media_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": att["mime"],
                               "data": base64.b64encode(att["bytes"]).decode()},
                })
            elif att["kind"] == "pdf":
                media_blocks.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.b64encode(att["bytes"]).decode()},
                })
        if datasets:
            import data_analysis
            media_blocks.append({"type": "text", "text": data_analysis.dataset_summary(datasets)})
        existing = last["content"]
        if isinstance(existing, str):
            new_content = media_blocks + [{"type": "text", "text": existing}]
        else:
            new_content = media_blocks + existing
        working_messages[-1] = {"role": "user", "content": new_content}

    yielded_text = False
    for _ in range(12):
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=system_prompt(),
            tools=ALL_TOOLS,
            messages=working_messages,
        )

        for block in response.content:
            if block.type == "text" and block.text:
                yield {"type": "text", "text": block.text}
                yielded_text = True
            elif block.type == "tool_use":
                yield {"type": "tool_use", "name": block.name, "input": block.input}

        working_messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result, chart_spec, export = _execute_tool(block.name, block.input, datasets)
            if chart_spec is not None:
                yield {"type": "chart", "spec": chart_spec}
            else:
                yield {"type": "tool_result", "name": block.name, "output": result}
                if export is not None:
                    yield {"type": "export", "name": block.name, "export": export}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })
        working_messages.append({"role": "user", "content": tool_results})

    if not yielded_text:
        yield {"type": "text", "text": (
            "I wasn't able to produce an answer for that. Try asking for a specific "
            "metric or column from your data and I'll compute it directly."
        )}
    yield {"type": "done"}


# ===========================================================
# Google Gemini
# ===========================================================

def _claude_schema_to_gemini(schema: dict) -> dict:
    """Gemini accepts OpenAPI-style schemas — same shape as Anthropic's input_schema."""
    if not schema.get("properties"):
        return {
            "type": "object",
            "properties": {
                "_unused": {"type": "string", "description": "no parameters needed"}
            },
        }
    return schema


def _chat_gemini(messages, model, api_key, attachments=None, datasets=None) -> Iterator[dict]:
    from google import genai
    from google.genai import types as gt

    attachments = attachments or []
    datasets = datasets or []
    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in your secrets / env.")
    client = genai.Client(api_key=api_key)
    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    function_declarations = [
        gt.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_claude_schema_to_gemini(t["input_schema"]),
        )
        for t in ALL_TOOLS
    ]
    tools_config = [gt.Tool(function_declarations=function_declarations)]

    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    # Attach knowledge-base screenshots to the first user turn as reference images
    kb_images = knowledge_base.images_for_context()
    if kb_images and contents and contents[0]["role"] == "user":
        ref_parts = []
        for img in kb_images:
            ref_parts.append({"text": f"[Reference image: {img['category']} - {img['title']}]"})
            ref_parts.append(gt.Part.from_bytes(data=img["bytes"], mime_type=img["mime"]))
        contents[0]["parts"] = ref_parts + contents[0]["parts"]

    # Attach THIS turn's uploaded files (images + PDFs, sent to Gemini natively) and
    # the spreadsheet summary to the latest user turn.
    if (attachments or datasets) and contents and contents[-1]["role"] == "user":
        extra_parts = []
        for att in attachments:
            if att["kind"] in ("image", "pdf"):
                mime = "application/pdf" if att["kind"] == "pdf" else att["mime"]
                extra_parts.append(gt.Part.from_bytes(data=att["bytes"], mime_type=mime))
        if datasets:
            import data_analysis
            extra_parts.append({"text": data_analysis.dataset_summary(datasets)})
        contents[-1]["parts"] = extra_parts + contents[-1]["parts"]

    config = gt.GenerateContentConfig(
        system_instruction=system_prompt(),
        tools=tools_config,
    )

    yielded_text = False
    malformed_retries = 0
    for _ in range(12):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        candidate = response.candidates[0] if response.candidates else None
        parts = (candidate.content.parts
                 if candidate and candidate.content and candidate.content.parts else None)

        if not parts:
            # Gemini can return no usable parts - most often a MALFORMED_FUNCTION_CALL
            # (it mangled a tool's arguments, e.g. multi-line analyze_data code) or a
            # safety/length stop. Surface it and retry once by nudging the model.
            reason = getattr(candidate, "finish_reason", None) if candidate else None
            reason = getattr(reason, "name", str(reason)) if reason is not None else "UNKNOWN"
            if reason == "MALFORMED_FUNCTION_CALL" and malformed_retries < 2:
                malformed_retries += 1
                contents.append({"role": "user", "parts": [{"text": (
                    "Your previous tool call could not be parsed. If you were calling "
                    "analyze_data, keep the `code` as a SINGLE simple statement (e.g. "
                    "assign to `result`), avoid backslashes and triple-quotes, and try "
                    "again. Otherwise, just answer in plain text."
                )}]})
                continue
            if not yielded_text:
                yield {"type": "text", "text": (
                    "I couldn't complete that with the file this time "
                    f"(model stop reason: `{reason}`). Please try rephrasing, or ask "
                    "the specific number/column you need and I'll compute it directly."
                )}
            break

        model_parts = []
        tool_calls = []
        for part in parts:
            if getattr(part, "text", None):
                yield {"type": "text", "text": part.text}
                yielded_text = True
                model_parts.append({"text": part.text})
            fc = getattr(part, "function_call", None)
            if fc:
                args = dict(fc.args) if fc.args else {}
                args.pop("_unused", None)
                yield {"type": "tool_use", "name": fc.name, "input": args}
                model_parts.append({"function_call": {"name": fc.name, "args": args}})
                tool_calls.append((fc.name, args))

        if model_parts:
            contents.append({"role": "model", "parts": model_parts})

        if not tool_calls:
            break

        response_parts = []
        for name, args in tool_calls:
            result, chart_spec, export = _execute_tool(name, args, datasets)
            if chart_spec is not None:
                yield {"type": "chart", "spec": chart_spec}
            else:
                yield {"type": "tool_result", "name": name, "output": result}
                if export is not None:
                    yield {"type": "export", "name": name, "export": export}
            response_parts.append({
                "function_response": {
                    "name": name,
                    "response": {"result": result},
                }
            })
        contents.append({"role": "user", "parts": response_parts})

    if not yielded_text:
        yield {"type": "text", "text": (
            "I wasn't able to produce an answer for that. Try asking for a specific "
            "metric or column from your data and I'll compute it directly."
        )}
    yield {"type": "done"}
