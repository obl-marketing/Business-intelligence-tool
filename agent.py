"""Provider-agnostic agent loop. Routes to Anthropic or Gemini.

The chat() function yields events of the form:
  {"type": "text", "text": str}
  {"type": "tool_use", "name": str, "input": dict}
  {"type": "tool_result", "name": str, "output": str}
  {"type": "done"}

Messages are passed in normalized text-only form across turns:
  [{"role": "user"|"assistant", "content": "string"}, ...]

The provider-specific tool-use loop happens inside each implementation and
is not surfaced as messages between turns (each new user message gets a fresh
agent loop with just the text history for context).
"""
from __future__ import annotations
import datetime as _dt
import os
from typing import Iterator

from tools import TOOL_SCHEMAS, run_tool
import mock_data


def system_prompt() -> str:
    today = _dt.date.today().isoformat()
    return f"""You are an AI Data Scientist embedded in a Business Intelligence tool. \
The user has connected three marketing data sources and asks you questions through chat.

Connected sources:
- **Google Analytics 4** - site traffic, events, funnels, products, lead forms
- **Google Ads** - account/campaign/keyword performance (spend, clicks, conversions, ROAS)
- **Meta Ads** (Facebook + Instagram) - campaigns and per-creative performance

Today's date is {today}. The available data covers {mock_data.DATA_START.isoformat()} \
to {mock_data.DATA_END.isoformat()}.

How you work:
1. When a user asks a question, decide which tools to call. You can call multiple tools \
   across multiple turns - chain them as needed. For cross-channel questions (e.g. \
   "where should I shift budget?"), pull data from multiple sources and compare.
2. Always ground your answers in the actual data returned by tools. Cite specific numbers.
3. When the user asks for analysis (drop-offs, ROAS, top performers, etc.), do not just \
   dump the data. Interpret it: explain WHAT the numbers mean, WHY they might be \
   happening, and WHAT the user should do about it.
4. Structure deeper analyses as:
   - **Findings** (the key numbers, with evidence)
   - **Analysis** (your interpretation of what's driving the numbers)
   - **Recommended Strategies** (concrete, prioritised actions the user can take)
5. Use markdown formatting. Tables for comparative data. Bold for key numbers.
6. If a question is ambiguous (e.g. "this month" without specifying), pick a reasonable \
   range and state your assumption.
7. If the user asks about a period outside the available data range, say so and offer \
   the closest available range.
8. For ads questions, always think about ROAS, CPA, and wasted spend. For organic \
   (GA4) questions, think about conversion rate, drop-off, and traffic mix.

You are not just a query tool - you are an analyst. Your job is to find loopholes, \
spot opportunities, and help the user grow their business with evidence-backed strategy."""


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

    for _ in range(10):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt(),
            tools=TOOL_SCHEMAS,
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
            result = run_tool(block.name, block.input)
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
        # Gemini doesn't accept empty-property objects; insert a dummy optional
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

    # Build the Gemini tool config from our shared TOOL_SCHEMAS
    function_declarations = [
        gt.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=_claude_schema_to_gemini(t["input_schema"]),
        )
        for t in TOOL_SCHEMAS
    ]
    tools_config = [gt.Tool(function_declarations=function_declarations)]

    # Build initial contents (prior text turns)
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    config = gt.GenerateContentConfig(
        system_instruction=system_prompt(),
        tools=tools_config,
    )

    for _ in range(10):
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
                args.pop("_unused", None)  # remove our schema dummy if Gemini fills it
                yield {"type": "tool_use", "name": fc.name, "input": args}
                model_parts.append({"function_call": {"name": fc.name, "args": args}})
                tool_calls.append((fc.name, args))

        if model_parts:
            contents.append({"role": "model", "parts": model_parts})

        if not tool_calls:
            break

        response_parts = []
        for name, args in tool_calls:
            result = run_tool(name, args)
            yield {"type": "tool_result", "name": name, "output": result}
            response_parts.append({
                "function_response": {
                    "name": name,
                    "response": {"result": result},
                }
            })
        contents.append({"role": "user", "parts": response_parts})

    yield {"type": "done"}
