# AI Data Scientist - Chat-based BI Tool

A chat-based business intelligence tool that connects to your data sources and acts like an AI data scientist. Currently ships with Google Analytics (mock data for the demo) and powered by Claude.

Ask questions like:
- *"How many page views did I get in May?"*
- *"Analyse my user journey and tell me where there's a drop-off."*
- *"Go through my GA4 events and tell me my top viewed products."*
- *"Which of my lead forms perform best, and which need work?"*

The agent calls GA4 tools, grounds answers in real data, and gives you **findings, analysis, and recommended strategies** - not just numbers.

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
- `query_pageviews` - traffic over time or by page
- `query_events` - GA4 events with counts and unique users
- `analyze_user_journey` - funnel with drop-off at each step
- `query_top_products` - product views, add-to-cart, purchases, revenue
- `query_form_performance` - lead form views, submissions, conversion rates
- `get_data_coverage` - what date range data is available

---

## Connecting real Google Analytics

The mock-data layer is the only thing standing between you and real GA4. To swap it in:

1. Create a GCP service account, enable the **Google Analytics Data API**, and give it Viewer access to your GA4 property.
2. Download the service account JSON.
3. Install the GA4 client:
   ```bash
   pip install google-analytics-data
   ```
4. Set in `.env`:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/abs/path/to/service-account.json
   GA4_PROPERTY_ID=123456789
   DATA_SOURCE=ga4
   ```
5. In `tools.py`, replace each `mock_data.*` call with a real GA4 `BetaAnalyticsDataClient.run_report(...)` call. The tool schemas the agent sees don't need to change - same date ranges, same return shape.

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
