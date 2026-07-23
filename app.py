"""Streamlit chat UI for the BI tool. Supports Anthropic Claude and Google Gemini."""
from __future__ import annotations
import json
import os

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from agent import chat
import knowledge_base
import chat_store
import doc_extract

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


# Copy data-source secrets into env vars so the non-Streamlit layers
# (tools.py / ga4_client.py) can read them.
for _key in ("DATA_SOURCE", "GA4_PROPERTY_ID", "GA4_SERVICE_ACCOUNT_JSON",
             "GA4_SERVICE_ACCOUNT_FILE", "SITE_BASE_URL",
             "GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_BRANCH"):
    _val = _get_secret(_key)
    if _val:
        os.environ[_key] = _val

import persistent_store
_PERSISTENT = persistent_store.github_enabled()

_GA4_LIVE = (
    os.environ.get("DATA_SOURCE", "mock").lower() == "ga4"
    and bool(os.environ.get("GA4_PROPERTY_ID"))
    and bool(os.environ.get("GA4_SERVICE_ACCOUNT_JSON")
             or os.environ.get("GA4_SERVICE_ACCOUNT_FILE"))
)


PROVIDER_MODELS = {
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
}
PROVIDER_DEFAULT_MODEL = {
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-2.5-flash",
}

st.set_page_config(page_title="STARS — Self-Trained Analyst for Reporting & Strategy",
                   page_icon="⭐", layout="wide")

# Shared-password gate (active only when APP_PASSWORD is set in secrets/env)
import auth
auth.require_login()
_USER = auth.current_user()

# ---------- Sidebar ----------
with st.sidebar:
    st.title("STARS ⭐")
    st.caption("**S**elf-**T**rained **A**nalyst for **R**eporting & **S**trategy")
    auth.logout_button()

    _kb_n = knowledge_base.count()
    page = st.radio(
        "Mode",
        options=["Chat", f"Training ({_kb_n})"],
        horizontal=True,
        label_visibility="collapsed",
    )
    page = "Training" if page.startswith("Training") else "Chat"

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

    _site_base = os.environ.get("SITE_BASE_URL", "").rstrip("/")
    _frontend_label = (
        f"Frontend audit  —  ✅ ENABLED ({_site_base})" if _site_base
        else "Frontend audit  —  available (set SITE_BASE_URL)"
    )
    with st.expander(_frontend_label, expanded=False):
        st.markdown(
            "**What it does:** the agent can fetch any page on your site live and "
            "read its CTAs, forms, trust signals, copy, headings, schema, and "
            "structure. Combined with the GA4 behavior data, it can give specific "
            "UX/UI/conversion feedback - e.g. *'page X has 65% bounce in GA4 AND "
            "the audit shows the only CTA is below 1,400 words → fix Y.'*\n\n"
            "**Setup (optional but recommended):** add your site to Streamlit secrets so "
            "the agent can audit by path:\n"
            "```toml\nSITE_BASE_URL = \"https://www.yoursite.com\"\n```\n"
            "Without it, you'd pass full URLs every time (e.g. *'audit "
            "https://yoursite.com/products/abc'*).\n\n"
            "**Limitation:** sees server-rendered HTML only. JavaScript-injected "
            "content (SPA modals, lazy-loaded sections, A/B variants) is not captured."
        )

    _ga4_label = (
        "Google Analytics 4  —  ✅ LIVE" if _GA4_LIVE
        else "Google Analytics 4  —  connected (mock)"
    )
    with st.expander(_ga4_label, expanded=False):
        if _GA4_LIVE:
            st.success(f"Querying live property {os.environ.get('GA4_PROPERTY_ID')}")
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
    st.subheader("Conversations")
    if st.button("+ New chat", use_container_width=True, type="primary"):
        st.session_state["active_chat_id"] = chat_store.new_chat(_USER)
        st.session_state["messages"] = []
        st.rerun()

    _saved_chats = chat_store.list_chats(_USER)
    _active = st.session_state.get("active_chat_id")
    if _saved_chats:
        for c in _saved_chats[:25]:
            is_active = c["id"] == _active
            col_a, col_b = st.columns([5, 1])
            with col_a:
                label = ("👉 " if is_active else "💬 ") + c["title"]
                if st.button(label, key=f"chat_{c['id']}", use_container_width=True,
                             help=f"{c['message_count']} messages · {c['updated_at']}"):
                    if c["id"] != _active:
                        loaded = chat_store.load_chat(c["id"], _USER)
                        st.session_state["active_chat_id"] = c["id"]
                        st.session_state["messages"] = loaded["messages"] if loaded else []
                        st.rerun()
            with col_b:
                if st.button("✕", key=f"del_chat_{c['id']}", help="Delete this chat"):
                    chat_store.delete_chat(c["id"], _USER)
                    if c["id"] == _active:
                        st.session_state["active_chat_id"] = None
                        st.session_state["messages"] = []
                    st.rerun()
    else:
        st.caption("No saved chats yet. Ask a question to start one.")

    st.divider()
    st.subheader("Try asking")
    examples = [
        "How is my website traffic doing in the last 30 days?",
        "Break down my traffic by channel.",
        "Analyse my user journey and tell me where there's a drop-off.",
        "Audit my homepage and tell me what's hurting conversion.",
        "Find my highest-bounce page, audit it, and tell me how to fix it.",
        "Review my lead forms across the site - which need the most work?",
        "How can I extract more leads from my top traffic pages?",
        "What's my Meta Ads ROAS by campaign?",
        "Which Google Ads campaigns are wasting spend?",
        "Compare Google Ads vs Meta Ads - where should I shift budget?",
    ]
    for ex in examples:
        if st.button(ex, key=f"ex_{hash(ex)}", use_container_width=True):
            st.session_state["pending_input"] = ex
            st.rerun()

    st.divider()
    if st.button("Clear current chat (don't save)", use_container_width=True):
        st.session_state["messages"] = []
        st.rerun()


# ================= TRAINING PAGE =================
if page == "Training":
    st.title("Training — teach the AI your business")
    st.caption(
        "Everything you add here is treated as **ground truth**. The AI uses it on "
        "every question - your form definitions, page behaviors, chatbot flows, event "
        "meanings, and learnings. Add screenshots and the AI can see them too."
    )

    _kb_dir = os.environ.get("KB_DIR", "knowledge")
    _data_external = os.path.isabs(_kb_dir) and not os.path.abspath(_kb_dir).startswith(os.getcwd())
    if _PERSISTENT:
        st.success(
            f"✅ Backed up to {persistent_store.storage_label()}. "
            "Entries survive reboots and app updates.",
            icon="💾",
        )
    elif _data_external:
        st.success(
            f"✅ Stored on this server at `{_kb_dir}` — persists across reboots and "
            "app updates. Use **Export** below for an extra backup any time.",
            icon="💾",
        )
    else:
        st.warning(
            "⚠️ Training data is stored inside the app folder, which a code update can "
            "overwrite. Ask your admin to set `KB_DIR` and `CHATS_DIR` to a folder "
            "outside the app (or enable GitHub backup). Use **Export** below to back up.",
            icon="💾",
        )

    # --- Add new entry ---
    with st.form("add_knowledge", clear_on_submit=True):
        st.subheader("Add knowledge")
        col1, col2 = st.columns([1, 2])
        with col1:
            category = st.selectbox("Category", knowledge_base.CATEGORIES)
        with col2:
            title = st.text_input(
                "Title / name",
                placeholder="e.g. Wholesale Inquiry Form, Tiles PLP, Support Chatbot",
            )
        description = st.text_area(
            "Definition / notes (optional if you upload a document below)",
            height=140,
            placeholder=(
                "Describe it in plain language, OR upload a document below and leave this "
                "blank to use the document's contents. Examples:\n"
                "- This form triggers as a popup after 30s on any /products/ page.\n"
                "- LEARNING: Treat 'generate_lead' as our true lead metric, not form_submit.\n"
                "- (Upload a price list / catalog / past report as Excel or PDF.)"
            ),
        )
        col_a, col_b = st.columns(2)
        with col_a:
            screenshot = st.file_uploader(
                "Screenshot (optional)", type=["png", "jpg", "jpeg", "webp", "gif"]
            )
        with col_b:
            document = st.file_uploader(
                "Document (Excel / CSV / PDF / Word / text)",
                type=["xlsx", "xls", "csv", "pdf", "docx", "txt", "md"],
                help="Text is extracted and becomes the AI's knowledge. Great for price "
                     "lists, product catalogs, past reports, SOPs. Old .doc: save as .docx first.",
            )
        submitted = st.form_submit_button("Add to knowledge base", type="primary")
        if submitted:
            doc_text = ""
            doc_note = ""
            if document is not None:
                with st.spinner(f"Reading {document.name}..."):
                    doc_text, kind = doc_extract.extract_text(document.name, document.read())
                if not doc_text:
                    st.error(f"Couldn't read {document.name}. Supported: .xlsx .xls .csv "
                             ".pdf .docx .txt .md")
                    st.stop()
                doc_note = f"\n\n[Extracted from uploaded file: {document.name}]\n{doc_text}"

            final_title = title.strip() or (document.name if document else "")
            final_description = (description.strip() + doc_note).strip()

            if not final_title:
                st.error("Please give it a title (or upload a document to use its name).")
            elif not final_description:
                st.error("Add a description or upload a document.")
            else:
                img_bytes = screenshot.read() if screenshot else None
                img_mime = screenshot.type if screenshot else None
                knowledge_base.add_entry(category, final_title, final_description,
                                         img_bytes, img_mime)
                extra = f" (read {document.name})" if document else ""
                st.success(f"Added '{final_title}' to {category}{extra}.")
                st.rerun()

    st.divider()

    # --- Existing entries ---
    entries = knowledge_base.load_entries()
    st.subheader(f"Knowledge base ({len(entries)} entries)")

    if not entries:
        st.caption("Nothing yet. Add your first definition above.")
    else:
        # group by category
        for cat in knowledge_base.CATEGORIES:
            cat_items = [e for e in entries if e["category"] == cat]
            if not cat_items:
                continue
            st.markdown(f"#### {cat}  ·  {len(cat_items)}")
            for e in cat_items:
                with st.expander(f"{e['title']}", expanded=False):
                    st.markdown(e["description"])
                    if e.get("image_b64"):
                        import base64 as _b64
                        st.image(_b64.b64decode(e["image_b64"]), use_container_width=True)
                    st.caption(f"Added {e.get('created_at', '')}  ·  id `{e['id']}`")
                    if st.button("Delete", key=f"del_{e['id']}"):
                        knowledge_base.delete_entry(e["id"])
                        st.rerun()

    st.divider()

    # --- Import / Export ---
    st.subheader("Backup & restore")
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "Export knowledge base (JSON)",
            data=knowledge_base.export_json(),
            file_name="knowledge_base.json",
            mime="application/json",
            use_container_width=True,
        )
    with c2:
        uploaded_kb = st.file_uploader("Import a knowledge base JSON", type=["json"], key="kb_import")
        if uploaded_kb is not None:
            try:
                n = knowledge_base.import_json(uploaded_kb.read().decode(), merge=True)
                st.success(f"Imported {n} new entries.")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

    st.stop()  # don't render the chat page below


# ================= CHAT PAGE =================
# ---------- State ----------
# Each entry: {"role": "user"|"assistant", "content": "text", "display_blocks": [...]}
# `content` is text-only and is what the next API call sees. `display_blocks` is
# kept separately for re-rendering rich tool-call expanders on the screen.
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "active_chat_id" not in st.session_state:
    st.session_state["active_chat_id"] = None


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

    # --- Auto-save this conversation (private to the logged-in user) ---
    if not st.session_state.get("active_chat_id"):
        st.session_state["active_chat_id"] = chat_store.new_chat(_USER)
    chat_store.save_chat(st.session_state["active_chat_id"], st.session_state["messages"], _USER)
    st.rerun()  # refresh sidebar list to reflect new title/timestamp
