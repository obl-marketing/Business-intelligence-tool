"""Streamlit chat UI for the BI tool. Supports Anthropic Claude and Google Gemini."""
from __future__ import annotations
import json
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agent import chat

load_dotenv()


def _render_chart(spec: dict) -> None:
    """Render a chart spec emitted by the agent using Streamlit native charts."""
    try:
        x = spec["x"]
        df = pd.DataFrame(
            {s["name"]: s["values"] for s in spec["series"]},
            index=x,
        )
        st.caption(f"**{spec.get('title', 'Chart')}**")
        ctype = spec.get("chart_type", "bar")
        if ctype == "line":
            st.line_chart(df)
        elif ctype == "area":
            st.area_chart(df)
        else:
            st.bar_chart(df)
    except Exception as e:
        st.warning(f"Could not render chart: {e}")


def _get_secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets (cloud) or env vars (local), whichever has it."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except (FileNotFoundError, Exception):
        pass
    return os.environ.get(key, default)


PROVIDER_MODELS = {
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
}
PROVIDER_DEFAULT_MODEL = {
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-2.5-flash",
}

st.set_page_config(page_title="AI Data Scientist", page_icon="[chart]", layout="wide")

# ---------- Sidebar ----------
with st.sidebar:
    st.title("AI Data Scientist")
    st.caption("Chat-based BI over your marketing data.")

    # Provider selection
    secret_provider = _get_secret("LLM_PROVIDER", "anthropic").lower()
    if secret_provider not in PROVIDER_MODELS:
        secret_provider = "anthropic"
    provider = st.selectbox(
        "Provider",
        options=["anthropic", "gemini"],
        index=["anthropic", "gemini"].index(secret_provider),
        format_func=lambda p: "Anthropic Claude" if p == "anthropic" else "Google Gemini",
    )

    # API key for chosen provider
    if provider == "anthropic":
        api_key = _get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            api_key = st.text_input("Anthropic API Key", type="password",
                                    help="Set ANTHROPIC_API_KEY in secrets.")
    else:
        api_key = _get_secret("GEMINI_API_KEY") or _get_secret("GOOGLE_API_KEY")
        if not api_key:
            api_key = st.text_input("Gemini API Key", type="password",
                                    help="Set GEMINI_API_KEY in secrets. Get one free at https://aistudio.google.com/apikey.")

    # Model picker for chosen provider
    model_options = PROVIDER_MODELS[provider]
    secret_model_key = "ANTHROPIC_MODEL" if provider == "anthropic" else "GEMINI_MODEL"
    model_default = _get_secret(secret_model_key, PROVIDER_DEFAULT_MODEL[provider])
    if model_default not in model_options:
        model_default = PROVIDER_DEFAULT_MODEL[provider]
    model = st.selectbox("Model", options=model_options, index=model_options.index(model_default))

    st.divider()
    st.subheader("Data sources")
    st.caption("Demo date range: 2026-03-01 to 2026-06-04")

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
        "How many page views did I get in May 2026?",
        "Analyse my user journey and tell me where there's a drop-off.",
        "Go through my GA4 events and tell me my top viewed products.",
        "Analyse all my lead forms and tell me the best and worst performers.",
        "Which Google Ads campaigns are wasting spend?",
        "Find my worst-performing Google Ads keywords - candidates for negative keywords.",
        "What's my Meta Ads ROAS by campaign?",
        "Which Meta Ads creatives are working and which are fatigued?",
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
# Each entry: {"role": "user"|"assistant", "content": "text", "display_blocks": [...]}
# `content` is text-only and is what the next API call sees. `display_blocks` is
# kept separately for re-rendering rich tool-call expanders on the screen.
if "messages" not in st.session_state:
    st.session_state["messages"] = []


# ---------- Render history ----------
def _render_blocks(blocks: list[dict]) -> None:
    for b in blocks:
        if b["kind"] == "text":
            st.markdown(b["content"])
        elif b["kind"] == "chart":
            _render_chart(b["spec"])
        elif b["kind"] == "tool":
            with st.expander(f"Evidence: `{b['name']}`  -  args: `{json.dumps(b['input'])}`"):
                try:
                    st.json(json.loads(b["output"]))
                except Exception:
                    st.code(b["output"])


for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        _render_blocks(msg.get("display_blocks", [{"kind": "text", "content": msg["content"]}]))


# ---------- Handle input ----------
prompt = st.chat_input("Ask about your analytics...")
if "pending_input" in st.session_state:
    prompt = st.session_state.pop("pending_input")

if prompt:
    if not api_key:
        st.error(f"Please provide a {provider.title()} API key (sidebar or secrets).")
        st.stop()

    # api_history: just text turns
    api_history = [{"role": m["role"], "content": m["content"]} for m in st.session_state["messages"]]
    api_history.append({"role": "user", "content": prompt})

    # Display + persist user turn
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state["messages"].append({
        "role": "user",
        "content": prompt,
        "display_blocks": [{"kind": "text", "content": prompt}],
    })

    # Stream assistant turn
    with st.chat_message("assistant"):
        display_blocks: list[dict] = []
        accumulated_text = ""
        text_area = st.empty()
        tool_status_by_call: dict[int, object] = {}

        try:
            for event in chat(api_history, model=model, api_key=api_key, provider=provider):
                etype = event["type"]
                if etype == "text":
                    accumulated_text += event["text"]
                    display_blocks.append({"kind": "text", "content": event["text"]})
                    text_area.markdown(accumulated_text)
                elif etype == "chart":
                    text_area = st.empty()
                    accumulated_text = ""
                    _render_chart(event["spec"])
                    display_blocks.append({"kind": "chart", "spec": event["spec"]})
                elif etype == "tool_use":
                    if event["name"] == "render_chart":
                        continue  # chart rendering is shown via the chart event itself
                    # Close current text region, open a status box for the tool
                    text_area = st.empty()
                    accumulated_text = ""
                    placeholder = st.status(f"Gathering evidence: `{event['name']}`...", expanded=False)
                    call_index = len(display_blocks)
                    tool_status_by_call[call_index] = placeholder
                    display_blocks.append({
                        "kind": "tool",
                        "name": event["name"],
                        "input": event["input"],
                        "output": "",
                    })
                elif etype == "tool_result":
                    # Fill the most recent empty tool block with this name
                    for i in range(len(display_blocks) - 1, -1, -1):
                        b = display_blocks[i]
                        if b["kind"] == "tool" and b["name"] == event["name"] and not b["output"]:
                            b["output"] = event["output"]
                            placeholder = tool_status_by_call.get(i)
                            if placeholder is not None:
                                with placeholder:
                                    try:
                                        st.json(json.loads(event["output"]))
                                    except Exception:
                                        st.code(event["output"])
                                placeholder.update(label=f"Evidence: `{event['name']}`", state="complete")
                            break
                    text_area = st.empty()
                elif etype == "done":
                    pass
        except Exception as e:
            st.error(f"Error: {e}")
            st.stop()

    # Persist normalized assistant turn (text only) + rich blocks for re-rendering
    assistant_text = "".join(b["content"] for b in display_blocks if b["kind"] == "text")
    st.session_state["messages"].append({
        "role": "assistant",
        "content": assistant_text,
        "display_blocks": display_blocks,
    })
