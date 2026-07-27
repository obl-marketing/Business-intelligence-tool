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


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# Storage-safety cap for a spreadsheet attached in Training (kept in the shared,
# GitHub-backed knowledge base). NOT an analysis limit - files under this are
# analysed in full, every row.
_MAX_TRAINING_FILE_BYTES = 40 * 1024 * 1024  # 40 MB


def _df_download_buttons(df, seed: str, filename: str = "stars_analysis") -> None:
    """Render CSV + Excel download buttons for a DataFrame."""
    if df is None or getattr(df, "empty", True):
        return
    import io
    try:
        csv_bytes = df.to_csv(index=False).encode("utf-8")
    except Exception:
        return
    xlsx_bytes = None
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            df.to_excel(xw, index=False, sheet_name="Analysis")
        xlsx_bytes = buf.getvalue()
    except Exception:
        xlsx_bytes = None
    cols = st.columns(2)
    with cols[0]:
        st.download_button("⬇️ CSV", data=csv_bytes, file_name=f"{filename}.csv",
                           mime="text/csv", key=f"csv_{seed}", use_container_width=True)
    if xlsx_bytes is not None:
        with cols[1]:
            st.download_button("⬇️ Excel", data=xlsx_bytes, file_name=f"{filename}.xlsx",
                               mime=_XLSX_MIME, key=f"xlsx_{seed}", use_container_width=True)


def _table_from_tool_output(output: str):
    """Best-effort: pull a tabular structure out of a tool-result JSON string so
    GA4/ads query results become downloadable. Returns a DataFrame or None."""
    try:
        obj = json.loads(output)
    except Exception:
        return None
    rows = None
    if isinstance(obj, list):
        rows = obj
    elif isinstance(obj, dict):
        for k in ("rows", "funnel", "data", "keywords", "creatives", "campaigns", "matched_pages"):
            v = obj.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                rows = v
                break
    if not rows or not isinstance(rows[0], dict):
        return None
    try:
        return pd.DataFrame(rows)
    except Exception:
        return None


def _df_from_csv(csv_text: str):
    import io
    try:
        return pd.read_csv(io.StringIO(csv_text))
    except Exception:
        return None


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
    auth.password_change_ui()
    auth.manage_users_ui()

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
        st.session_state["chat_datasets"] = []
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
                        st.session_state["chat_datasets"] = []
                        st.rerun()
            with col_b:
                if st.button("✕", key=f"del_chat_{c['id']}", help="Delete this chat"):
                    chat_store.delete_chat(c["id"], _USER)
                    if c["id"] == _active:
                        st.session_state["active_chat_id"] = None
                        st.session_state["messages"] = []
                        st.session_state["chat_datasets"] = []
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
                help="Excel/CSV are stored as FULL data (no row limit) and analysed "
                     "exactly by the AI for every user, in every future chat. PDF/Word/"
                     "text have their text extracted as knowledge. Old .doc: save as "
                     ".docx first.",
            )
        submitted = st.form_submit_button("Add to knowledge base", type="primary")
        if submitted:
            doc_note = ""
            entry_datasets: list[dict] = []
            if document is not None:
                ext = document.name.rsplit(".", 1)[-1].lower() if "." in document.name else ""
                raw = document.read()
                if ext in ("xlsx", "xls", "csv"):
                    # Spreadsheets are stored as FULL data (no character limit) and
                    # loaded into the analysis engine - every row, for every user.
                    if len(raw) > _MAX_TRAINING_FILE_BYTES:
                        st.error(
                            f"{document.name} is {len(raw)/1e6:.0f} MB. To keep the shared "
                            f"knowledge base loadable, attached data files are capped at "
                            f"{_MAX_TRAINING_FILE_BYTES//10**6} MB (this is a storage limit, "
                            "not an analysis limit - files under it are analysed in full). "
                            "Split the file or upload a focused subset.")
                        st.stop()
                    import data_analysis
                    with st.spinner(f"Loading {document.name}..."):
                        built = data_analysis.build_dataframes(document.name, raw, 0)
                    if not built:
                        st.error(f"Couldn't read any tables from {document.name}.")
                        st.stop()
                    for b in built:
                        entry_datasets.append({
                            "name": b["label"],
                            "csv": b["df"].to_csv(index=False),
                            "rows": b["rows"],
                            "cols": b["cols"],
                        })
                    total_rows = sum(b["rows"] for b in built)
                    doc_note = (f"\n\n[Attached dataset: {document.name} — "
                                f"{total_rows:,} rows across {len(built)} table(s), "
                                "fully available for analysis.]")
                else:
                    # PDF / Word / text: extract readable text into the description.
                    with st.spinner(f"Reading {document.name}..."):
                        doc_text, kind = doc_extract.extract_text(document.name, raw)
                    if not doc_text:
                        st.error(f"Couldn't read {document.name}. Supported: .xlsx .xls .csv "
                                 ".pdf .docx .txt .md")
                        st.stop()
                    doc_note = f"\n\n[Extracted from uploaded file: {document.name}]\n{doc_text}"

            final_title = title.strip() or (document.name if document else "")
            final_description = (description.strip() + doc_note).strip()

            if not final_title:
                st.error("Please give it a title (or upload a document to use its name).")
            elif not final_description and not entry_datasets:
                st.error("Add a description or upload a document.")
            else:
                img_bytes = screenshot.read() if screenshot else None
                img_mime = screenshot.type if screenshot else None
                knowledge_base.add_entry(category, final_title, final_description,
                                         img_bytes, img_mime, datasets=entry_datasets)
                if entry_datasets:
                    extra = (f" — dataset loaded ({sum(d['rows'] for d in entry_datasets):,} "
                             "rows, all analysable)")
                elif document:
                    extra = f" (read {document.name})"
                else:
                    extra = ""
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
                    for _ds in e.get("datasets", []) or []:
                        st.caption(f"📊 Dataset: **{_ds.get('name','data')}** — "
                                   f"{_ds.get('rows',0):,} rows × {_ds.get('cols',0)} cols "
                                   "(fully analysable, no row limit)")
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
# Spreadsheet/CSV DataFrames uploaded in the current chat, kept alive for the
# whole session so follow-up questions can keep analysing them. Reset when the
# user starts or switches chats (see sidebar).
if "chat_datasets" not in st.session_state:
    st.session_state["chat_datasets"] = []


# ---------- Render history ----------
def _render_blocks(blocks: list[dict], seed: str = "live") -> None:
    for bi, b in enumerate(blocks):
        bseed = f"{seed}_{bi}"
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
                _df_download_buttons(_table_from_tool_output(b["output"]), bseed,
                                     filename=f"{b['name']}_result")
        elif b["kind"] == "export":
            n, m = b.get("rows", 0), b.get("cols", 0)
            note = "  (first 50,000 rows)" if b.get("truncated") else ""
            st.caption(f"⬇️ Download this result — {n:,} rows × {m} columns{note}")
            _df_download_buttons(_df_from_csv(b.get("csv", "")), bseed,
                                 filename=b.get("filename", "stars_analysis"))
        elif b["kind"] == "files":
            st.caption("📎 Attached: " + ", ".join(b.get("names", [])))


for _mi, msg in enumerate(st.session_state["messages"]):
    with st.chat_message(msg["role"]):
        _render_blocks(msg.get("display_blocks", [{"kind": "text", "content": msg["content"]}]),
                       seed=f"m{_mi}")


# ---------- Handle input ----------
# Attachment types. Screenshots + PDFs go to the model natively; Excel/CSV
# become pandas DataFrames the agent computes over exactly.
_IMG_EXT = ("png", "jpg", "jpeg", "webp", "gif")
_TABLE_EXT = ("xlsx", "xls", "csv")
_DOC_EXT = ("docx", "txt", "md")


# Datasets attached in Training are shared + persistent, so every chat can
# analyse them. Cache the parsed DataFrames; re-parse only when Training changes.
@st.cache_data(show_spinner=False)
def _training_datasets_cached(sig):
    return knowledge_base.training_dataframes()


_training_datasets = _training_datasets_cached(knowledge_base.training_dataset_signature())

if _training_datasets or st.session_state["chat_datasets"]:
    _bits = [f"{d['label']} ({d['rows']:,}×{d['cols']})"
             for d in _training_datasets + st.session_state["chat_datasets"]]
    st.caption("📊 Data available to analyse: " + "  ·  ".join(_bits))

# The 📎 attach button lives INSIDE the chat bar, right next to the text field.
_user_input = st.chat_input(
    "Ask about your analytics, or attach a file...",
    accept_file="multiple",
    file_type=list(_IMG_EXT) + ["pdf"] + list(_TABLE_EXT) + list(_DOC_EXT),
)
prompt = None
chat_uploads = []
if _user_input is not None:
    if isinstance(_user_input, str):
        prompt = _user_input
    else:  # ChatInputValue: has .text and .files (attribute or dict access)
        prompt = (_user_input.get("text") if isinstance(_user_input, dict)
                  else getattr(_user_input, "text", None))
        _files = (_user_input.get("files") if isinstance(_user_input, dict)
                  else getattr(_user_input, "files", None))
        chat_uploads = _files or []
if "pending_input" in st.session_state:
    prompt = st.session_state.pop("pending_input")
if not prompt and chat_uploads:
    prompt = "Please analyse the attached file(s) in detail."

if prompt:
    if not api_key:
        st.error(f"Please provide a {provider.title()} API key (sidebar or secrets).")
        st.stop()

    # ----- Process this turn's attachments -----
    attachments: list[dict] = []          # native image/pdf parts for the model
    attached_names: list[str] = []
    doc_texts: list[str] = []             # extracted text from docx/txt/md
    for uf in (chat_uploads or []):
        ext = uf.name.rsplit(".", 1)[-1].lower() if "." in uf.name else ""
        raw = uf.read()
        attached_names.append(uf.name)
        try:
            if ext in _IMG_EXT:
                attachments.append({"kind": "image", "mime": uf.type or f"image/{ext}",
                                    "bytes": raw, "name": uf.name})
            elif ext == "pdf":
                attachments.append({"kind": "pdf", "bytes": raw, "name": uf.name})
            elif ext in _TABLE_EXT:
                import data_analysis
                start = len(st.session_state["chat_datasets"])
                built = data_analysis.build_dataframes(uf.name, raw, start)
                if built:
                    st.session_state["chat_datasets"].extend(built)
                else:
                    st.warning(f"Couldn't read any tables from {uf.name}.")
            elif ext in _DOC_EXT:
                text, _kind = doc_extract.extract_text(uf.name, raw)
                if text:
                    doc_texts.append(f"[Attached document: {uf.name}]\n{text}")
        except Exception as e:
            st.error(f"Failed to process {uf.name}: {e}")

    # All DataFrames the agent can analyse: Training datasets (shared/persistent)
    # first, then this chat's uploads - renumbered df1..dfN so the model sees one
    # clean namespace.
    _combined = _training_datasets + st.session_state["chat_datasets"]
    datasets = [{**d, "var": f"df{i + 1}"} for i, d in enumerate(_combined)]

    # Model-facing prompt: user's text + any extracted doc text
    model_prompt = prompt
    if doc_texts:
        model_prompt = prompt + "\n\n" + "\n\n".join(doc_texts)

    # api_history: just text turns
    api_history = [{"role": m["role"], "content": m["content"]} for m in st.session_state["messages"]]
    api_history.append({"role": "user", "content": model_prompt})

    # Display + persist user turn
    with st.chat_message("user"):
        st.markdown(prompt)
        for att in attachments:
            if att["kind"] == "image":
                st.image(att["bytes"], width=280)
        if attached_names:
            st.caption("📎 Attached: " + ", ".join(attached_names))
    _user_blocks = [{"kind": "text", "content": prompt}]
    if attached_names:
        _user_blocks.append({"kind": "files", "names": attached_names})
    st.session_state["messages"].append({
        "role": "user",
        "content": model_prompt,
        "display_blocks": _user_blocks,
    })

    # Stream assistant turn
    with st.chat_message("assistant"):
        display_blocks: list[dict] = []
        accumulated_text = ""
        text_area = st.empty()
        tool_status_by_call: dict[int, object] = {}

        try:
            for event in chat(api_history, model=model, api_key=api_key, provider=provider,
                              attachments=attachments, datasets=datasets):
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
                elif etype == "export":
                    # Full computed table available for download (rendered after rerun
                    # via _render_blocks; show a live confirmation here).
                    exp = event["export"]
                    display_blocks.append({
                        "kind": "export",
                        "csv": exp.get("csv", ""),
                        "rows": exp.get("rows", 0),
                        "cols": exp.get("cols", 0),
                        "truncated": exp.get("truncated", False),
                        "filename": exp.get("filename", "stars_analysis"),
                    })
                    st.caption(f"📥 Result ready to download ({exp.get('rows', 0):,} rows).")
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
