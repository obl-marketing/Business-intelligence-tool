# STARS — project context for Claude

STARS is a Streamlit chat app that acts as an AI data analyst for Orient Bell's
marketing team. Users (often non-technical, e.g. a category head) ask questions in
plain language and STARS answers from Google Analytics 4, Google Ads, Meta Ads and
QuickLook dealer usage. Provider: **Google Gemini** (`LLM_PROVIDER=gemini`).

## Deploy / update — READ THIS FIRST (it's easy to get wrong)

- **The live server deploys from branch `claude/relaxed-volta-hvi2d`.** Always push
  finished work there so it can go live. (Each Claude-on-web session also has its own
  scratch branch, but that one is NOT what the server watches — fast-forward it onto
  `claude/relaxed-volta-hvi2d` and push that.)
- **Server:** self-hosted Ubuntu, app at `/opt/stars`, runs as systemd service `stars`
  under a locked-down `stars` user. `/opt/stars` is mode `700` (private to `stars`).
- **The GitHub "auto-deploy" Action is NOT configured** (its `DEPLOY_HOST`/SSH secrets
  are empty, so it fails on every push — this is pre-existing, not a code problem).
  Until those secrets are set, deploying is a **manual pull on the server**.
- **The one command to update the server** (run as the `stars` user — never `cd` into
  `/opt/stars` as another user, the `700` perms will "permission denied"):
  ```bash
  sudo -u stars -H git -C /opt/stars pull && sudo systemctl restart stars
  ```
  Verify: `sudo -u stars -H git -C /opt/stars rev-parse --short HEAD` matches the
  pushed commit, and `systemctl --no-pager status stars` says `active (running)`.
- The repo remote is HTTPS. No new Python dependencies have been added, so a plain
  `git pull` + restart is enough (no `pip install` needed for the current changes).

## Key architecture the tool depends on

- `app.py` — Streamlit UI. `agent.py` — provider-agnostic agent loop (Gemini/Anthropic)
  + the system prompt. `tools.py` — tool schemas + dispatch. `ga4_client.py` — live GA4.
  `frontend_audit.py` — live page fetch/render + URL resolution. `planner.py` — the
  Gemini pre-flight planner. `knowledge_base.py` + `knowledge/entries.json` — user's
  "Training" context, injected into the system prompt.
- Live data needs `DATA_SOURCE=ga4` + `GA4_PROPERTY_ID` + `GA4_SERVICE_ACCOUNT_JSON`,
  and `SITE_BASE_URL=https://www.orientbell.com` for URL resolution/audit. Set in
  `/opt/stars/stars.env`.

## Event handling — the property does NOT use standard GA4 names (important)

This GA4 property uses custom events (often `mkt-...`) and custom dimensions. In
particular, **forms AND popups are one lifecycle event, `mkt-form-event`, carrying
`customEvent:popup_id` (the form, e.g. ask-tile-expert / book-a-consultation /
tareeq-figurine) and `customEvent:action` = viewed/closed/submitted.** There is no
`form_view` / `form_submit` / `popup_view`.

Therefore the tools **self-discover** the schema instead of assuming standard names:
- `ga4_client.discover_schema()` / `list_custom_dimensions()` read the property's real
  events + custom dimensions (metadata API). Tool: `discover_ga4_schema`.
- `adaptive_lead_breakdown()` powers `form_breakdown` / `popup_breakdown`: it detects
  the real id + action dimensions and derives views/closes/submits from live data,
  with a legacy standard-event fallback. Do NOT reintroduce hard-coded event names.
- `frontend_audit.resolve_page_url()` (tool `resolve_page_url`) turns a plain page name
  ("flexi tiles") into the real URL + GA4 `page_path`, so users never paste links; the
  audit and GA4 then run on the SAME page.
- `planner.preflight()` runs before any API call: it resolves URLs + discovers the real
  schema and drafts a fetch plan, injected into the system prompt. Fully guarded.

## Zoho CRM (Leads & Deals / Opportunities)

- `zoho_client.py` handles OAuth (refresh-token → access-token) and `get`/`post`.
  `zoho_crm.py` (tools `query_zoho_leads`, `query_zoho_deals`, `discover_zoho_values`)
  runs native **COQL** against the Leads and Deals modules. `zoho_opportunities.py` is
  the OLD custom-endpoint (`ChatbotDeals`) scaffold — superseded by `zoho_crm.py`.
- **Config, not hard-coded:** field/module API names come from env
  (`ZOHO_SOURCE_FIELD`=Lead_Source, `ZOHO_SUBSOURCE_FIELD`=Sub_Source,
  `ZOHO_STAGE_FIELD`=Stage, modules Leads/Deals). Date keys map created→Created_Time,
  modified→Modified_Time, closing→Closing_Date. Data centre is **.com**
  (`ZOHO_ACCOUNTS_URL=https://accounts.zoho.com`).
- **Counting rule (confirmed):** a converted lead is flagged Converted but STAYS in
  Leads. So TOTAL leads = the Leads count (never Leads + Deals — that double-counts);
  Deals = post-qualification subset; qualification rate = Deals ÷ Leads. Leads =
  pre-qualification universe.
- **Don't guess source/stage spelling** — `discover_zoho_values` lists the real
  Lead_Source/Sub_Source/Stage values. Needs one live validation pass once the refresh
  token is set (COQL aggregate/field behaviour verified against the live property).

**Policy: don't rely on hand-written event definitions.** STARS discovers events itself.
Keep the Knowledge Base for business context it CANNOT infer from GA4 (page-structure
rules, brand/definition rules); avoid re-adding raw event-name dictionaries — the three
contradicting "Event Definitions" entries were removed because they confused the model.

## Conventions

- Don't hard-code GA4 event names in tools; discover them.
- Keep new pre-flight/discovery code fully guarded (never block the chat on failure).
- Match the existing file style; tools return JSON strings via `run_tool`.
