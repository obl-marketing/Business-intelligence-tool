"""Streamlit chat UI for the BI tool."""
from __future__ import annotations
import json
import os

import streamlit as st
from dotenv import load_dotenv

from agent import chat

load_dotenv()

st.set_page_config(page_title="AI Data Scientist", page_icon="[chart]", layout="wide")

# ---------- Sidebar ----------
with st.sidebar:
    st.title("AI Data Scientist")
    st.caption("Chat-based BI over your Google Analytics data.")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = st.text_input("Anthropic API Key", type="password",
                                help="Set ANTHROPIC_API_KEY in .env to skip this.")

    model = st.selectbox(
        "Model",
        options=["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
        index=["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"].index(
            os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
        ),
    )

    st.divider()
    st.subheader("Data source")
    st.success("Connected: Mock GA4 (demo data)")
    st.caption("Date range: 2026-03-01 to 2026-06-04")

    st.divider()
    st.subheader("Try asking")
    examples = [
        "How many page views did I get in May 2026?",
        "Analyse my user journey and tell me where there's a drop-off.",
        "Go through my GA4 events and tell me my top viewed products.",
        "Analyse all my lead forms and tell me the best and worst performers.",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{hash(ex)}", use_container_width=True):
            st.session_state["pending_input"] = ex
            st.rerun()

    st.divider()
    if st.button("Clear conversation", use_container_width=True):
        st.session_state["messages"] = []
        st.rerun()


# ---------- State ----------
if "messages" not in st.session_state:
    st.session_state["messages"] = []


# ---------- Render history ----------
def _render_assistant_blocks(blocks: list[dict]) -> None:
    for b in blocks:
        if b["kind"] == "text":
            st.markdown(b["content"])
        elif b["kind"] == "tool":
            with st.expander(f"Tool: `{b['name']}`  -  args: `{json.dumps(b['input'])}`"):
                try:
                    parsed = json.loads(b["output"])
                    st.json(parsed)
                except Exception:
                    st.code(b["output"])


for msg in st.session_state["messages"]:
    role = msg.get("display_role", msg["role"])
    if role == "tool_internal":
        continue  # tool results - kept in history for the API but not shown
    if role == "user":
        with st.chat_message("user"):
            st.markdown(msg["display_content"])
    else:
        with st.chat_message("assistant"):
            _render_assistant_blocks(msg["display_blocks"])


# ---------- Handle input ----------
prompt = st.chat_input("Ask about your analytics...")
if "pending_input" in st.session_state:
    prompt = st.session_state.pop("pending_input")

if prompt:
    if not api_key:
        st.error("Please provide an Anthropic API key (sidebar or .env).")
        st.stop()

    # display user message immediately
    with st.chat_message("user"):
        st.markdown(prompt)

    # build the API message history (no display fields)
    api_history = []
    for m in st.session_state["messages"]:
        api_history.append({"role": m["role"], "content": m["content"]})
    api_history.append({"role": "user", "content": prompt})

    # persist user message for display
    st.session_state["messages"].append({
        "role": "user",
        "content": prompt,
        "display_role": "user",
        "display_content": prompt,
    })

    # stream assistant response
    with st.chat_message("assistant"):
        display_blocks: list[dict] = []
        tool_placeholders: dict[str, object] = {}
        text_area = st.empty()
        accumulated_text = ""
        final_messages = api_history

        try:
            for event in chat(api_history, model=model, api_key=api_key):
                if event["type"] == "text":
                    accumulated_text += event["text"]
                    display_blocks.append({"kind": "text", "content": event["text"]})
                    text_area.markdown(accumulated_text)
                elif event["type"] == "tool_use":
                    text_area = st.empty()  # close current text region
                    accumulated_text = ""
                    label = f"Calling `{event['name']}`..."
                    placeholder = st.status(label, expanded=False)
                    tool_placeholders[event["name"] + json.dumps(event["input"], sort_keys=True)] = placeholder
                    display_blocks.append({
                        "kind": "tool",
                        "name": event["name"],
                        "input": event["input"],
                        "output": "",
                    })
                elif event["type"] == "tool_result":
                    # update the last matching tool block
                    for b in reversed(display_blocks):
                        if b["kind"] == "tool" and b["name"] == event["name"] and not b["output"]:
                            b["output"] = event["output"]
                            break
                    # find the matching placeholder and close it
                    for key, placeholder in list(tool_placeholders.items()):
                        if key.startswith(event["name"]):
                            with placeholder:
                                try:
                                    st.json(json.loads(event["output"]))
                                except Exception:
                                    st.code(event["output"])
                            placeholder.update(label=f"Done: `{event['name']}`", state="complete")
                            del tool_placeholders[key]
                            break
                    text_area = st.empty()
                elif event["type"] == "done":
                    final_messages = event["messages"]
        except Exception as e:
            st.error(f"Error: {e}")
            st.stop()

    # persist assistant turn(s) - keep the full API history for next turn
    # the last message in final_messages is the assistant; remember everything after the user turn
    user_idx = len(api_history) - 1  # index of the user message we just sent
    new_turns = final_messages[user_idx + 1:]
    for turn in new_turns:
        if turn["role"] == "assistant":
            st.session_state["messages"].append({
                "role": "assistant",
                "content": turn["content"],
                "display_role": "assistant",
                "display_blocks": display_blocks,
            })
        else:
            # tool_result user turns - keep for context but not displayed
            st.session_state["messages"].append({
                "role": "user",
                "content": turn["content"],
                "display_role": "tool_internal",
                "display_blocks": [],
            })
