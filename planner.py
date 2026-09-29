"""Pre-flight planner - the layer that runs BEFORE the agent touches any data API.

For a website / analytics question it does two things, in order:

1. **Deterministic grounding** (no LLM, always safe): if the question names a
   page / product / category, resolve it to REAL URLs via the sitemap; if GA4 is
   live, discover the property's REAL event names and custom dimensions. This is
   how the tool "figures out the URLs and the events itself" instead of the user
   pasting links or us shipping hand-written event definitions.

2. **A Gemini plan** (the pre-call the product owner asked for): Gemini turns the
   question + those discovered facts into a short, concrete fetch plan - which
   page_path to use, what date range, which GA4 tools to call, which real events
   matter, and whether to also audit the page for the "why".

The result is injected into the agent's system prompt for that turn, so the agent
executes a grounded plan rather than guessing. Everything here is best-effort and
fully guarded: any failure returns "" and the agent proceeds exactly as before.
"""
from __future__ import annotations
import datetime as _dt
import json
import os


# Words that signal the question is about the website / analytics (vs. dealer
# usage, ads, or an uploaded file), i.e. worth grounding with URLs + GA4 schema.
_SITE_HINTS = (
    "page", "engagement", "traffic", "visit", "bounce", "demand", "product",
    "category", "plp", "pdp", "form", "popup", "pop-up", "funnel", "journey",
    "session", "views", "click", "convert", "conversion", "landing", "url",
    "audit", "site", "website", "tile", "tiles", "enquir", "lead",
)
# Words that signal a DIFFERENT source - skip page/schema grounding for these.
_NON_SITE_HINTS = (
    "dealer", "quicklook", "branch", "zone", "merchant", "google ads",
    "meta ads", "facebook", "instagram", "campaign", "roas", "spend", "keyword",
)


def _looks_like_site_question(q: str) -> bool:
    low = q.lower()
    if any(h in low for h in _NON_SITE_HINTS) and not any(
        h in low for h in ("page", "engagement", "traffic", "form", "popup")
    ):
        return False
    return any(h in low for h in _SITE_HINTS)


_CRM_HINTS = (
    "lead", "deal", "opportunit", "crm", "zoho", "salesperson", "sales person",
    "sales rep", "fls", "pipeline", "qualif", "closed won", "closed lost", "won",
    "conversion rate", "sub-source", "sub source", "stage", "closing", "dealer",
    "zone", "branch", "enquir", "funnel", "volume",
)


def _looks_like_crm_question(q: str) -> bool:
    return any(h in q.lower() for h in _CRM_HINTS)


def _zoho_facts(question: str) -> dict | None:
    """Ground CRM questions: the in-use field catalog + counting rules + how to
    filter, so the planner picks the right module/fields and prefers populated
    ones. Guarded - returns None if Zoho isn't connected."""
    try:
        import zoho_crm
        if not zoho_crm.is_active():
            return None
        in_use = [{"label": f["label"], "api_name": f["api_name"],
                   "meaning": (f.get("meaning") or "")[:60]}
                  for f in zoho_crm._catalog().get("fields", []) if f.get("in_use")]
        return {
            "modules": {"leads": "PRE-qualification: ALL leads incl. converted = the "
                                 "true total (converted leads stay in Leads). Use for "
                                 "lead counts + source/sub-source/channel analysis. Does "
                                 "NOT hold volume/stage/salesperson/branch-conversion.",
                        "deals": "POST-qualification opportunities. Use for VOLUME "
                                 "(Volume_In_Sq_Mtr sq.mtr/sq.ft), stage, won/lost, "
                                 "open(New+Active) vs closed, SALESPERSON performance, "
                                 "and conversion by branch/zone/area."},
            "module_routing": "Volume, stage, won/lost, salesperson/FLS, branch, zone, "
                              "area, open-vs-closed -> DEALS. How-many-leads / which "
                              "channel-or-source -> LEADS. If genuinely ambiguous which "
                              "module, tell the agent to ask ONE short clarifying "
                              "question offering both, rather than hunting blindly.",
            "counting_rule": "TOTAL leads RECEIVED = Leads module + Deals module (a "
                             "qualified lead moves into Deals, so the modules are separate "
                             "sets) -> use query_total_leads, which returns the split + "
                             "combined total. Qualification rate = Deals / (Leads+Deals).",
            "friendly_group_by_keys": ["month", "salesperson", "status", "stage",
                                       "source", "sub_source", "dealer", "zone", "branch"],
            "how_to_query": "Filters (zone/branch/salesperson/status/dealer/category) go "
                            "in the `filters` dict, NOT top-level. 'How much volume/"
                            "amount/revenue' → metric='sum' + sum_field (volume|amount|"
                            "won_amount). Time series → group_by='month'.",
            "date_and_stage_rules": "DATE FIELD: default 'created'; use 'closing' ONLY "
                            "for CLOSED-lead questions (won/lost/junk); 'modified' for "
                            "recently-moved. OPEN/PENDING = Stage Category New + Active "
                            "(filters={'status':['New','Active']}, date_field='created'). "
                            "CLOSED won/lost = date_field='closing' + stage filter.",
            "prioritise_populated": "Prefer fields that are actually filled; if unsure "
                                    "which column a request maps to, call zoho_field_usage "
                                    "to check fill-rate and pick the populated one, or ask "
                                    "ONE short follow-up. Skip near-empty fields.",
            "in_use_fields": in_use,
        }
    except Exception:
        return None


def _default_range(days: int = 28) -> tuple[str, str]:
    today = _dt.date.today()
    return (today - _dt.timedelta(days=days)).isoformat(), today.isoformat()


def _ga4_live() -> bool:
    try:
        import ga4_client
        return ga4_client.is_active()
    except Exception:
        return False


def _gather_facts(question: str) -> dict:
    """Deterministically resolve page URLs and discover GA4 schema for grounding."""
    facts: dict = {}

    if os.environ.get("SITE_BASE_URL", "").strip():
        try:
            import frontend_audit
            resolved = frontend_audit.resolve_page_url(question, limit=6)
            if resolved.get("best_match") or resolved.get("all_candidates"):
                facts["resolved_pages"] = {
                    "best_match": resolved.get("best_match"),
                    "category_or_plp_pages": resolved.get("category_or_plp_pages"),
                    "product_pages": resolved.get("product_pages"),
                }
        except Exception:
            pass

    if _ga4_live():
        try:
            import ga4_client
            start, end = _default_range()
            schema = ga4_client.discover_schema(start, end, top_n=30)
            facts["ga4_schema"] = {
                "top_events": [e["event_name"] for e in schema.get("top_events", [])][:30],
                "custom_dimensions": schema.get("custom_dimensions"),
                "lead_or_form_id_dimensions": schema.get("lead_or_form_id_dimensions"),
                "action_dimensions": schema.get("action_dimensions"),
                "schema_window": {"start": start, "end": end},
            }
        except Exception:
            pass

    if _looks_like_crm_question(question):
        zf = _zoho_facts(question)
        if zf:
            facts["zoho"] = zf

    return facts


_PLAN_INSTRUCTION = """You are the planning step of a business-intelligence agent \
used by a non-technical marketing lead. Given the user's question and the FACTS \
already discovered (real page URLs, GA4 events/dimensions, and/or the Zoho CRM field \
catalog), output a SHORT fetch plan the agent will follow.

Rules:
- Never invent event names, URLs, or field api_names. Only use ones in FACTS. If FACTS \
lacks something, say to discover it (resolve_page_url / discover_ga4_schema / \
discover_zoho_fields) rather than guess.
- GA4 "engagement / traffic / demand for <page>": pick the resolved page_path (prefer a \
category/PLP page), plan query_page_metrics (+ query_pages_engagement_ranked) and \
audit_page(url) for the "why". Forms/popups: query_popup_breakdown / query_form_breakdown.
- CRM (leads/deals/opportunities/salesperson/pipeline/won): use FACTS.zoho. FIRST route \
to the right MODULE (see FACTS.zoho.module_routing): Leads = raw lead counts + source/ \
channel; Deals = volume/stage/won-lost/salesperson/branch/zone/open-vs-closed. If the \
question is genuinely ambiguous about which module, say to ask the user ONE short \
clarifying question offering both, rather than guessing. Put \
zone/branch/salesperson/status/dealer/category in the `filters` dict (NOT top-level - \
top-level is ignored). "How much volume/amount/revenue" → metric='sum' + the right \
sum_field (volume|amount|won_amount). Monthly → group_by='month'. Use ONLY api_names \
from FACTS.zoho.in_use_fields; if the field is missing/ambiguous or might be near-empty, \
say to call zoho_field_usage / discover_zoho_fields or ask ONE short follow-up. Prefer \
populated fields over empty ones.
- Keep it to 4-7 bullet steps. Be concrete: name the exact page_path/module/field, tool, \
filters, and date range. Convert relative dates using today's date.

Output plain text, starting with 'FETCH PLAN:'."""


def _gemini_plan(question: str, facts: dict, api_key: str | None, model: str | None) -> str:
    """One Gemini call: question + discovered facts -> concrete fetch plan text."""
    from google import genai
    from google.genai import types as gt

    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return ""
    client = genai.Client(api_key=api_key)
    model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    today = _dt.date.today().isoformat()
    prompt = (
        f"Today's date: {today}\n\n"
        f"USER QUESTION:\n{question}\n\n"
        f"FACTS (discovered for this site - use ONLY these):\n"
        f"{json.dumps(facts, indent=2, default=str)}\n"
    )
    resp = client.models.generate_content(
        model=model,
        contents=[{"role": "user", "parts": [{"text": prompt}]}],
        config=gt.GenerateContentConfig(system_instruction=_PLAN_INSTRUCTION),
    )
    return (getattr(resp, "text", "") or "").strip()


def preflight(question: str, provider: str | None = None,
              api_key: str | None = None, model: str | None = None) -> str:
    """Return a system-prompt addendum grounding this turn, or '' if not applicable.

    Fully guarded - never raises. `api_key`/`model` are the Gemini creds/model when
    the chat runs on Gemini; the plan step is skipped (facts still injected) otherwise.
    """
    try:
        if not question or not (_looks_like_site_question(question)
                                or _looks_like_crm_question(question)):
            return ""

        facts = _gather_facts(question)
        if not facts:
            return ""

        plan_text = ""
        # The plan call uses Gemini regardless of the chat provider, but only if a
        # Gemini key is available; otherwise the discovered facts alone still help.
        # The plan step sees the FULL facts (incl. the whole Zoho field catalog).
        try:
            gem_key = api_key if (provider or "").lower() == "gemini" else None
            plan_text = _gemini_plan(question, facts, gem_key, model)
        except Exception:
            plan_text = ""

        # For the MAIN agent prompt, trim the big Zoho field list (the plan already
        # named the fields it needs) to keep the prompt lean — leave a pointer.
        inject_facts = dict(facts)
        if isinstance(inject_facts.get("zoho"), dict):
            zc = dict(inject_facts["zoho"])
            n = len(zc.get("in_use_fields") or [])
            zc.pop("in_use_fields", None)
            zc["field_catalog"] = (f"{n} in-use fields available - call "
                                   "discover_zoho_fields for the full label→api_name→"
                                   "meaning list; zoho_field_usage for fill-rates.")
            inject_facts["zoho"] = zc

        parts = [
            "",
            "# PRE-FLIGHT GROUNDING (computed for you THIS turn - use it, don't re-guess)",
            "",
            "Before you call any tool, these facts were already resolved for this "
            "question so you fetch from the RIGHT source with the RIGHT fields:",
            "",
            "```json",
            json.dumps(inject_facts, indent=2, default=str)[:6000],
            "```",
        ]
        if plan_text:
            parts += ["", "## Suggested fetch plan", plan_text[:2500]]
        parts += [
            "",
            "Use the resolved page_path/url values and REAL field/event names directly "
            "(don't ask the user for a link or guess standard names). For CRM, put "
            "zone/branch/salesperson/status in the `filters` dict, prefer populated "
            "fields (zoho_field_usage), and use discover_zoho_fields for any column not "
            "listed. If something needed isn't resolved, discover it or ask ONE short "
            "follow-up rather than guessing.",
            "",
        ]
        return "\n".join(parts)
    except Exception:
        return ""
