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
        "(a DataFrame, Series, number, or dict); print() output is also captured. "
        "pandas is available as `pd` and numpy as `np`. Do not import anything or read/"
        "write files - the DataFrames are already in memory. If your code errors, read the "
        "error and call analyze_data again with a fix."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python/pandas code operating on df1, df2, ... Assign the answer to "
                    "`result`. Example: result = df1.groupby('Category')['Sales'].sum()"
                    ".sort_values(ascending=False)"
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
    if ga4_live:
        return (
            "**Google Analytics 4 is LIVE** - queries hit the user's real GA4 property, "
            "which typically retains up to 14 months of history. "
            "Google Ads and Meta Ads are still DEMO data covering "
            f"{mock_data.DATA_START.isoformat()} to {mock_data.DATA_END.isoformat()} - "
            "if the user asks about ads, answer from the demo data but remind them it is "
            "sample data until those sources are connected."
        )
    return (
        f"The available data covers {mock_data.DATA_START.isoformat()} to "
        f"{mock_data.DATA_END.isoformat()} (demo dataset)."
    )


def system_prompt() -> str:
    today = _dt.date.today().isoformat()
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

Today's date is {today}. {_data_coverage_note()}

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
case skip the GA4 lookup unless behavior data adds context. The audit only sees \
server-rendered HTML; if a page is heavily JavaScript-rendered, say so and ask the user \
to share a screenshot.

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

    for _ in range(12):
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        candidate = response.candidates[0] if response.candidates else None
        if not candidate or not candidate.content or not candidate.content.parts:
            break

        model_parts = []
        tool_calls = []
        for part in candidate.content.parts:
            if getattr(part, "text", None):
                yield {"type": "text", "text": part.text}
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

    yield {"type": "done"}
