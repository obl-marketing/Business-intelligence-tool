"""Claude agent loop with GA4 tool use."""
from __future__ import annotations
import datetime as _dt
import json
import os
from typing import Iterator

import anthropic

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
) -> Iterator[dict]:
    """Run the agent loop. Yields dicts describing what's happening.

    Yields events of the form:
      {"type": "text", "text": str}            - assistant text (final or interim)
      {"type": "tool_use", "name": str, "input": dict}
      {"type": "tool_result", "name": str, "output": str}
      {"type": "done", "messages": list}       - updated message history
    """
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

    working_messages = list(messages)

    # Safety cap on tool-calling iterations
    for _ in range(10):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt(),
            tools=TOOL_SCHEMAS,
            messages=working_messages,
        )

        # Emit any text blocks for streaming display
        for block in response.content:
            if block.type == "text" and block.text:
                yield {"type": "text", "text": block.text}
            elif block.type == "tool_use":
                yield {"type": "tool_use", "name": block.name, "input": block.input}

        # Append assistant turn to history
        working_messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break

        # Execute every tool_use block, collect results
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

    yield {"type": "done", "messages": working_messages}
