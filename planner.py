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

    return facts


_PLAN_INSTRUCTION = """You are the planning step of a business-intelligence agent \
used by a non-technical marketing lead. Given the user's question and the FACTS \
already discovered for this site (real page URLs and the property's real GA4 event \
names + custom dimensions), output a SHORT fetch plan the agent will follow.

Rules:
- Never invent event names or URLs. Only use the ones in FACTS. If FACTS lacks \
something, say to discover it (resolve_page_url / discover_ga4_schema) rather than guess.
- For "engagement / traffic / demand for <page>": pick the resolved page_path \
(prefer a category/PLP page for category demand), then plan query_page_metrics \
(+ query_pages_engagement_ranked for context) over a sensible date range, and \
audit_page(url) for the "why".
- For forms/popups: use query_popup_breakdown / query_form_breakdown (they \
auto-adapt to customEvent:popup_id + customEvent:action).
- Keep it to 4-7 bullet steps. Be concrete: name the exact page_path, tool, and \
date range. Convert relative dates using today's date.

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
        if not question or not _looks_like_site_question(question):
            return ""

        facts = _gather_facts(question)
        if not facts:
            return ""

        plan_text = ""
        # The plan call uses Gemini regardless of the chat provider, but only if a
        # Gemini key is available; otherwise the discovered facts alone still help.
        try:
            gem_key = api_key if (provider or "").lower() == "gemini" else None
            plan_text = _gemini_plan(question, facts, gem_key, model)
        except Exception:
            plan_text = ""

        parts = [
            "",
            "# PRE-FLIGHT GROUNDING (computed for you THIS turn - use it, don't re-guess)",
            "",
            "Before you call any tool, these facts were already resolved for this "
            "question so you fetch the RIGHT pages and REAL events, not assumptions:",
            "",
            "```json",
            json.dumps(facts, indent=2, default=str)[:6000],
            "```",
        ]
        if plan_text:
            parts += ["", "## Suggested fetch plan", plan_text[:2500]]
        parts += [
            "",
            "Use the resolved `page_path`/`url` values directly (don't ask the user "
            "for a link), and map the question to the REAL event names above rather "
            "than standard GA4 names. If a needed page or event isn't listed, call "
            "resolve_page_url / discover_ga4_schema to find it.",
            "",
        ]
        return "\n".join(parts)
    except Exception:
        return ""
