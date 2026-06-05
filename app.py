"""Streamlit chat UI for the BI tool."""
from __future__ import annotations
import json
import os

import streamlit as st
from dotenv import load_dotenv

from agent import chat

load_dotenv()


def _get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets (cloud) or env vars (local), whichever has it."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except (FileNotFoundError, Exception):
        pass
    return os.environ.get(key, default)

st.set_page_config(page_title="AI Data Scientist", page_icon="[chart]", layout="wide")

# ---------- Sidebar ----------
with st.sidebar:
    st.title("AI Data Scientist")
    st.caption("Chat-based BI over your Google Analytics data.")

    api_key = _get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = st.text_input("Anthropic API Key", type="password",
                                help="Set ANTHROPIC_API_KEY in .env (local) or Streamlit secrets (cloud).")

    model_default = _get_secret("ANTHROPIC_MODEL", "claude-opus-4-8")
    model_options = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]
    if model_default not in model_options:
        model_default = "claude-opus-4-8"
    model = st.selectbox("Model", options=model_options, index=model_options.index(model_default))

    st.divider()
    st.subheader("Data sources")
    st.caption("Demo date range: 2026-03-01 to 2026-06-04")

    # Google Analytics
    with st.expander("Google Analytics 4  —  connected (mock)", expanded=False):
        st.markdown(
            "**To connect your real GA4 property:**\n\n"
            "1. In Google Cloud Console, enable the **Google Analytics Data API**.\n"
            "2. Create a **service account** and download its JSON key.\n"
            "3. In GA4 → Admin → Property Access Management, add the service "
            "account email as a **Viewer**.\n"
            "4. Find your **GA4 Property ID** (Admin → Property Settings).\n"
            "5. Add to Streamlit secrets:\n"
            "```toml\n"
            'GA4_PROPERTY_ID = "123456789"\n'
            'GA4_SERVICE_ACCOUNT_JSON = """<paste the JSON contents here>"""\n'
            "DATA_SOURCE = \"ga4\"\n"
            "```\n"
            "6. Reboot the app. Mock data is replaced with real GA4 data."
        )

    # Google Ads
    with st.expander("Google Ads  —  connected (mock)", expanded=False):
        st.markdown(
            "**To connect your real Google Ads account:**\n\n"
            "1. Apply for a **Google Ads API developer token** "
            "(takes 1-2 business days).\n"
            "2. In Google Cloud Console, create an **OAuth 2.0 client** "
            "(type: Desktop or Web).\n"
            "3. Generate a **refresh token** using the OAuth playground or "
            "Google's `oauth2l` tool, scoped to `https://www.googleapis.com/auth/adwords`.\n"
            "4. Find your **Customer ID** in the Google Ads UI (top-right, "
            "format `123-456-7890`).\n"
            "5. Add to Streamlit secrets:\n"
            "```toml\n"
            'GOOGLE_ADS_DEVELOPER_TOKEN = "..."\n'
            'GOOGLE_ADS_CLIENT_ID = "..."\n'
            'GOOGLE_ADS_CLIENT_SECRET = "..."\n'
            'GOOGLE_ADS_REFRESH_TOKEN = "..."\n'
            'GOOGLE_ADS_CUSTOMER_ID = "1234567890"\n'
            "```\n"
            "6. Reboot the app."
        )

    # Meta Ads
    with st.expander("Meta Ads (Facebook + Instagram)  —  connected (mock)", expanded=False):
        st.markdown(
            "**To connect your real Meta Ads account:**\n\n"
            "1. Go to https://developers.facebook.com/apps and create a "
            "**Business app**.\n"
            "2. Add the **Marketing API** product to the app.\n"
            "3. Generate a **long-lived access token** with `ads_read` scope "
            "(Graph API Explorer → generate token → exchange for long-lived).\n"
            "4. Find your **Ad Account ID** (Business Manager → Ads Manager → "
            "settings; format `act_1234567890`).\n"
            "5. Add to Streamlit secrets:\n"
            "```toml\n"
            'META_ACCESS_TOKEN = "EAA..."\n'
            'META_AD_ACCOUNT_ID = "act_1234567890"\n'
            "```\n"
            "6. Reboot the app.\n\n"
            "Tokens expire every ~60 days; rotate via Business Manager."
        )

    st.divider()
    st.subheader("Try asking")
    examples = [
        # GA4
        "How many page views did I get in May 2026?",
        "Analyse my user journey and tell me where there's a drop-off.",
        "Go through my GA4 events and tell me my top viewed products.",
        "Analyse all my lead forms and tell me the best and worst performers.",
        # Google Ads
        "Which Google Ads campaigns are wasting spend?",
        "Find my worst-performing Google Ads keywords - candidates for negative keywords.",
        # Meta Ads
        "What's my Meta Ads ROAS by campaign?",
        "Which Meta Ads creatives are working and which are fatigued?",
        # Cross-channel
        "Compare Google Ads vs Meta Ads - where should I shift budget?",
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
