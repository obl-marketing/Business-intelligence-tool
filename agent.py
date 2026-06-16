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

ALL_TOOLS = TOOL_SCHEMAS + [CHART_TOOL]


def _execute_tool(name: str, args: dict) -> tuple[str, dict | None]:
    """Run a tool. Returns (result_json, chart_spec_or_None)."""
    if name == "render_chart":
        # Validate minimally; UI does the rendering
        ok = (
            isinstance(args.get("x"), list)
            and isinstance(args.get("series"), list)
            and all(isinstance(s, dict) and "values" in s for s in args["series"])
        )
        if not ok:
            return json.dumps({"error": "Invalid chart spec: need x[] and series[] with values"}), None
        return json.dumps({"status": "chart rendered to user"}), args
    return run_tool(name, args), None


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
    return f"""You are a senior data analyst and growth strategist embedded in a Business \
Intelligence tool. The user has connected their marketing data sources and chats with you \
to understand and grow their business. Many users are not analysts - your job is to make \
data feel simple while the analysis underneath stays rigorous.

Connected sources:
- **Google Analytics 4** - site traffic, events, funnels, products, lead forms
- **Google Ads** - account/campaign/keyword performance (spend, clicks, conversions, ROAS)
- **Meta Ads** (Facebook + Instagram) - campaigns and per-creative performance

Today's date is {today}. {_data_coverage_note()}

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
opportunities, quantify problems, and back every strategy with evidence."""


def chat(
    messages: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    provider: str | None = None,
) -> Iterator[dict]:
    """Dispatch to the right provider implementation."""
    provider = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()
    if provider == "gemini":
        yield from _chat_gemini(messages, model, api_key)
    else:
        yield from _chat_anthropic(messages, model, api_key)


# ===========================================================
# Anthropic Claude
# ===========================================================

def _chat_anthropic(messages, model, api_key) -> Iterator[dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

    working_messages = [{"role": m["role"], "content": m["content"]} for m in messages]

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
            result, chart_spec = _execute_tool(block.name, block.input)
            if chart_spec is not None:
                yield {"type": "chart", "spec": chart_spec}
            else:
                yield {"type": "tool_result", "name": block.name, "output": result}
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


def _chat_gemini(messages, model, api_key) -> Iterator[dict]:
    from google import genai
    from google.genai import types as gt

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
            result, chart_spec = _execute_tool(name, args)
            if chart_spec is not None:
                yield {"type": "chart", "spec": chart_spec}
            else:
                yield {"type": "tool_result", "name": name, "output": result}
            response_parts.append({
                "function_response": {
                    "name": name,
                    "response": {"result": result},
                }
            })
        contents.append({"role": "user", "parts": response_parts})

    yield {"type": "done"}
