# AI Data Scientist - Chat-based BI Tool

A chat-based business intelligence tool that connects to your data sources and acts like an AI data scientist. Ships with three sources wired up (mock data for the demo) and powered by Claude:

- **Google Analytics 4** - traffic, events, funnels, products, lead forms
- **Google Ads** - campaigns, keywords, spend, ROAS
- **Meta Ads** (Facebook + Instagram) - campaigns and per-creative performance

Ask questions like:
- *"How many page views did I get in May?"*
- *"Analyse my user journey and tell me where there's a drop-off."*
- *"Which Google Ads campaigns are wasting spend?"*
- *"What's my Meta Ads ROAS by creative format?"*
- *"Compare Google Ads vs Meta Ads — where should I shift budget?"*

The agent calls the right tools, grounds answers in real data, and gives you **findings, analysis, and recommended strategies** - not just numbers.

---

## Run it locally

### 1. Clone & install

```bash
git clone <your-repo-url>
cd Business-intelligence-tool

python3 -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Add your Anthropic API key

```bash
cp .env.example .env
# edit .env and paste your key
```

Get a key at https://console.anthropic.com/.

### 3. Run

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## How it works

```
+---------------+        +----------------+        +---------------+
|  Streamlit    | <----> |  Claude agent  | <----> |  GA4 tools    |
|  chat UI      |        |  (tool-use)    |        |  (mock data)  |
+---------------+        +----------------+        +---------------+
```

- **`app.py`** - Streamlit chat interface
- **`agent.py`** - Claude agent loop that decides which tools to call
- **`tools.py`** - GA4 tool definitions and dispatcher
- **`mock_data.py`** - Realistic synthetic GA4 data (Mar-Jun 2026)

The agent has these tools:

**Google Analytics 4**
- `query_pageviews` - traffic over time or by page
- `query_events` - GA4 events with counts and unique users
- `analyze_user_journey` - funnel with drop-off at each step
- `query_top_products` - product views, add-to-cart, purchases, revenue
- `query_form_performance` - lead form views, submissions, conversion rates

**Google Ads**
- `query_google_ads_summary` - account totals (spend, clicks, conversions, ROAS)
- `query_google_ads_campaigns` - per-campaign breakdown
- `query_google_ads_keywords` - per-keyword performance + match types

**Meta Ads**
- `query_meta_ads_summary` - account totals (spend, reach, CPM, ROAS)
- `query_meta_ads_campaigns` - per-campaign breakdown (with frequency for ad-fatigue)
- `query_meta_ads_creatives` - per-ad creative performance by format

**Meta**
- `get_data_coverage` - what date range data is available across sources

---

## Connecting real data sources

The sidebar in the app has step-by-step setup instructions for each source. Summary:

### Google Analytics 4
1. Enable the **Google Analytics Data API** in Google Cloud.
2. Create a service account, download its JSON, grant it Viewer on your GA4 property.
3. `pip install google-analytics-data`.
4. Set `GA4_PROPERTY_ID` and `GA4_SERVICE_ACCOUNT_JSON` in secrets.
5. In `tools.py`, replace `mock_data.*` calls with `BetaAnalyticsDataClient.run_report(...)`.

### Google Ads
1. Apply for a **Google Ads API developer token**.
2. Create OAuth 2.0 credentials, generate a refresh token.
3. `pip install google-ads`.
4. Set the five `GOOGLE_ADS_*` secrets shown in the sidebar.
5. In `tools.py`, replace `mock_data.google_ads_*` calls with `GoogleAdsClient` GAQL queries.

### Meta Ads
1. Create a Meta Business app, add Marketing API product.
2. Generate a long-lived access token with `ads_read`.
3. `pip install facebook-business`.
4. Set `META_ACCESS_TOKEN` and `META_AD_ACCOUNT_ID` in secrets.
5. In `tools.py`, replace `mock_data.meta_ads_*` calls with `AdAccount(...).get_insights(...)`.

The tool schemas the agent sees never change - the input dates and output shapes stay the same. Only the data layer behind `tools.py` swaps from mock to real.

---

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | - | Required. |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | Override in the sidebar or here. Use `claude-sonnet-4-6` for cheaper runs. |
| `DATA_SOURCE` | `mock` | Set to `ga4` once you wire in the real API. |

---

## Adding more data sources

The architecture is per-tool: each new source is a few more tools in `tools.py`. To add e.g. HubSpot:

1. Add tool schemas to `TOOL_SCHEMAS` (`query_hubspot_deals`, etc.).
2. Add their handlers in `run_tool()`.
3. Update the system prompt in `agent.py` so Claude knows the new capability exists.

The agent will route questions to the right tool based on the descriptions.
