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

- **Module routing (STARS must decide this FIRST; it's the #1 source of wrong/empty
  answers).** Leads = PRE-qualification: raw lead counts + source/sub-source/channel
  analysis; it does NOT hold volume/stage/salesperson/branch. Deals/Opportunity =
  POST-qualification: **volume** (`Volume_In_Sq_Mtr`), stage, won/lost, open (New+Active)
  vs closed, **salesperson** performance, and conversion by **branch/zone/area**.
  Heuristic: volume/stage/won-lost/salesperson/branch/zone/open-vs-closed → Deals;
  how-many-leads / which-channel → Leads. When genuinely ambiguous, STARS should **ask
  ONE short clarifying question offering both modules** rather than hunting blindly (and
  must never say a metric "doesn't exist" after checking only Leads — volume/stage/
  salesperson/branch all live in Deals). Encoded in agent.py's prompt + planner._zoho_facts.


- `zoho_client.py` handles OAuth (refresh-token → access-token) and `get`/`post`.
  `zoho_crm.py` (tools `query_zoho_leads`, `query_zoho_deals`, `discover_zoho_values`)
  runs native **COQL** against the Leads and Deals modules. `zoho_opportunities.py` is
  the OLD custom-endpoint (`ChatbotDeals`) scaffold — superseded by `zoho_crm.py`.
- **Config, not hard-coded:** field/module API names come from env
  (`ZOHO_SOURCE_FIELD`=Lead_Source, `ZOHO_SUBSOURCE_FIELD`=Sub_Source,
  `ZOHO_STAGE_FIELD`=Stage, modules Leads/Deals). Date keys map created→Created_Time,
  modified→Modified_Time, closing→Closing_Date. Data centre is **.com**
  (`ZOHO_ACCOUNTS_URL=https://accounts.zoho.com`).
- **Counting rule (per CRM owner, updated Sept 2026 — supersedes the old "converted
  stays in Leads" rule):** the Leads and Deals modules are SEPARATE record sets — a
  qualified lead moves INTO Deals. So **TOTAL leads received = Leads count + Deals count**
  (creation date) → use the `query_total_leads` tool / `zoho_crm.total_leads()`, which
  returns the per-module split plus the combined total (always show the split).
  Qualification rate = Deals ÷ (Leads + Deals). Date-field rule: default **created**;
  use **closing** ONLY for closed-lead questions (won/lost/junk). Open/pending = Stage
  Category **New + Active** (`filters={'status':['New','Active']}`, created date).
- **Don't guess source/stage spelling** — `discover_zoho_values` lists the real
  Lead_Source/Sub_Source/Stage values.
- **LIVE & validated** on the .com DC via COQL v8 (`ZOHO_API_VERSION=v8`). Gotchas
  fixed: COQL needs its own `ZohoCRM.coql.READ` scope; Zoho rejects the same aggregate
  in SELECT and ORDER BY (so `_agg_breakdown` sorts in Python, no ORDER BY); we trust
  the token's own `api_domain`.
- **Any field beyond source/sub_source/stage** (salesperson/owner/status): call
  `discover_zoho_fields(module)` to get real field API names, then pass the api_name as
  `group_by` to query_zoho_leads/deals (group_by now accepts any raw field name, plus
  the keys source/sub_source/stage/owner). Per-rep stale-lead analysis = Deals +
  `date_field='modified'` + `stage` filter + `group_by=<salesperson field>`.
- Prefer the live Zoho tools over any uploaded CRM spreadsheet exports.
- **Field dictionary: `docs/ZOHO_FIELDS.md`**. Field api_names CONFIRMED via COQL probe
  (Sept 2026): salesperson→`Sales_Person_Email_ID`, status→`Stage_Category`
  (New/Active/Closed), dealer→`Assigned_CP_Name`, zone→`Zone`, branch→`Branch_Area`,
  owner→`Owner`. **Deals sub-source = `Sub_source`** (lowercase s), Leads =
  `Sub_Source` — handled per-module. Friendly group_by keys resolve these; all
  env-overridable (`ZOHO_SALESPERSON_FIELD`, `ZOHO_STATUS_FIELD`, `ZOHO_DEALER_FIELD`,
  `ZOHO_ZONE_FIELD`, `ZOHO_BRANCH_FIELD`, `ZOHO_DEALS_SUBSOURCE_FIELD`).
- `discover_zoho_fields` lacks the settings scope, so it falls back to **COQL probing**
  of candidate api-names (works with the coql scope).
- **Query engine** (`_run`): supports `metric` count|sum (sum_field: volume→
  `Volume_In_Sq_Mtr` [text → summed in Python], amount→`Amount`, won_amount→
  `Won_Amount` [Currency → COQL SUM]); `group_by='month'` time series (loops per month);
  and a generic `filters` dict (equality on any field/friendly key). **Zone/branch/
  salesperson/status/etc. MUST go in `filters`** — there are no top-level args for them,
  and anything unrecognised is silently ignored (was a real wrong-numbers bug: a `zone`
  arg got dropped and returned unfiltered totals).
- UI: `app.py` shows a top-level `st.status` "Working…" from turn start to done, plus a
  per-tool status box, so the user always sees progress.
- **Full field catalog:** `data/zoho_deals_fields.json` (112 Deals columns: label,
  api_name, meaning, `in_use`). `discover_zoho_fields` serves it (all columns, meanings)
  with no metadata scope; `_dim_field`/`_measure_field` resolve human labels → api_names.
  Only 16 api_names are probe-confirmed; the rest are label-derived (fix per-field if a
  query errors, or add `ZohoCRM.settings.fields.READ` to auto-confirm all).
- **Planner grounds CRM too** (`planner._zoho_facts`, gated by `_looks_like_crm_question`):
  injects the in-use catalog + counting rules + how-to-filter into the Gemini pre-flight
  plan, so it decides module/filters/group_by/metric before querying and **prefers
  populated fields**. `zoho_field_usage` (tool) returns per-field fill-rate so STARS
  avoids near-empty columns; prefer it / a follow-up over guessing.

**Policy: don't rely on hand-written event definitions.** STARS discovers events itself.
Keep the Knowledge Base for business context it CANNOT infer from GA4 (page-structure
rules, brand/definition rules); avoid re-adding raw event-name dictionaries — the three
contradicting "Event Definitions" entries were removed because they confused the model.

## Conventions

- Don't hard-code GA4 event names in tools; discover them.
- Keep new pre-flight/discovery code fully guarded (never block the chat on failure).
- Match the existing file style; tools return JSON strings via `run_tool`.
