"""Generate a static HTML preview of the BI tool with sample Q&A using real tool outputs.

This is just for previewing the look-and-feel before the user installs anything.
Uses canned 'assistant' responses written to look like Claude's output, with the
underlying numbers coming from actual tool calls against the mock dataset.
"""
import json
import html
import tools

EXAMPLES = [
    {
        "q": "How many page views did I get in May 2026?",
        "tool_calls": [
            ("query_pageviews", {"start_date": "2026-05-01", "end_date": "2026-05-31", "group_by": "date"}),
        ],
        "answer_md": """## Findings

You received **{total:,} page views** in May 2026 across **{days} days**, averaging **{avg:,} views per day**.

| Period | Page Views |
|---|---:|
| First half (May 1-15) | {first_half:,} |
| Second half (May 16-31) | {second_half:,} |
| Change | **{delta_pct:+.1f}%** |

## Analysis

Traffic in the **first half of May was noticeably stronger** than the second half. Looking at the daily series, the dip starts around May 20 and persists through month-end. A few likely drivers worth investigating:

- A paid campaign or content push ending mid-month
- A seasonal shift in your customer base
- A change in organic ranking on a high-traffic page

## Recommended Strategies

1. **Pull a referrer breakdown for May 16-31 vs May 1-15** to identify whether the drop came from organic, paid, or direct traffic.
2. **Check Search Console** for any ranking drops on your top pages mid-month.
3. **Audit recent site changes** (deploys, content edits) between May 15-20 - the timing suggests something you control is involved.
"""
    },
    {
        "q": "Analyse my user journey and tell me where there's a drop-off.",
        "tool_calls": [
            ("analyze_user_journey", {"start_date": "2026-05-01", "end_date": "2026-05-31"}),
        ],
        "answer_md": """## Findings

Your funnel for May 2026:

| Step | Users | Conversion from previous | Drop-off |
|---|---:|---:|---:|
| 1. Session Start | {s1:,} | 100% | - |
| 2. Product View | {s2:,} | {c2}% | {d2}% |
| 3. **Add to Cart** | {s3:,} | **{c3}%** | **{d3}%** |
| 4. Begin Checkout | {s4:,} | {c4}% | {d4}% |
| 5. Add Payment Info | {s5:,} | {c5}% | {d5}% |
| 6. Purchase | {s6:,} | {c6}% | {d6}% |

The biggest drop is **Product View -> Add to Cart at {d3}%**. Of {s2:,} people who looked at a product, only {s3:,} added one to their cart.

## Analysis

A {c3}% view-to-cart rate isn't catastrophic for ceramics (luxury/considered purchase), but the magnitude here suggests friction in the product page itself - not the cart or checkout flow. The checkout steps (4 -> 5 -> 6) are actually healthy by ecom standards.

Likely culprits at the product page:
- **Pricing surprise** - shipping or taxes only revealed later
- **Trust signals missing** - no reviews, no return policy on-page
- **CTA visibility** - the Add to Cart button is below the fold or competing with other elements
- **Image quality** - ceramics demand strong visual proof of texture/finish

## Recommended Strategies

1. **Run a session replay** (Hotjar/Microsoft Clarity) on 50 product page sessions that didn't add to cart. Watch where they hesitate.
2. **A/B test moving the Add to Cart CTA** and putting shipping cost / return policy directly under it.
3. **Add reviews to product pages** if you don't have them. Even 3-5 reviews lift conversion meaningfully on considered purchases.
4. **Compare your top-viewed product's add-to-cart rate** to your worst - the gap will tell you which page-level changes matter most.
"""
    },
    {
        "q": "Go through my GA4 events and tell me my top viewed products.",
        "tool_calls": [
            ("query_top_products", {"start_date": "2026-05-01", "end_date": "2026-05-31"}),
        ],
        "answer_md": """## Findings

Your top products for May 2026, ranked by item views:

| # | Product | Views | Add to Cart | Purchases | Revenue | View -> Cart | Cart -> Purchase |
|---|---|---:|---:|---:|---:|---:|---:|
{rows}

**Top performer:** {top_name} ({top_views:,} views)
**Highest revenue:** {rev_name} (${rev_amt:,.0f})
**Best converter (view -> cart):** {best_conv_name} at {best_conv_rate}%

## Analysis

Views and revenue don't line up cleanly. **{top_name}** is the most-viewed product but **{rev_name}** generates the most revenue - which means your high-traffic product isn't your highest-value one.

Two distinct patterns to watch:
- **High views, low conversion** products are bringing people in but not closing. They may be acting as your "shop window" - useful for SEO/discovery even if they don't sell.
- **High conversion, low views** products are your hidden winners. Worth pushing more traffic to them.

## Recommended Strategies

1. **Prioritise homepage and category placement** for high-conversion products, not just high-view ones.
2. **For high-view, low-conversion products**, treat the page as an SEO landing page - add cross-sell modules pointing to better converters.
3. **Run a paid retargeting test** on visitors who viewed {best_conv_name} but didn't buy - the strong cart rate suggests they're high-intent.
4. **Audit pricing** on your top-viewed products. If view-to-cart is far below site average, pricing or value perception is the issue.
"""
    },
    {
        "q": "Analyse all my lead forms and tell me the best and worst performers.",
        "tool_calls": [
            ("query_form_performance", {"start_date": "2026-05-01", "end_date": "2026-05-31"}),
        ],
        "answer_md": """## Findings

Your lead form performance for May 2026:

| Form | Location | Type | Views | Submissions | Conversion |
|---|---|---|---:|---:|---:|
{rows}

**Best:** `{best_id}` at **{best_rate}%** ({best_subs} submissions from {best_views:,} views)
**Worst:** `{worst_id}` at **{worst_rate}%** ({worst_subs} submissions from {worst_views:,} views)

## Analysis

The spread between your best and worst form is **{spread:.0f}x** - that's not a forms problem, that's an intent and context problem.

**Why `{best_id}` works:** It sits in a high-intent context (wholesale buyers actively looking for a B2B path). The traffic is small but pre-qualified.

**Why `{worst_id}` doesn't:** Footer newsletter signups suffer from low attention - users have already decided to leave the page when they see it. Conversion at 2-3% is actually typical for footer-only newsletter forms, but you're spending real estate on it for almost no return.

**The hidden problem with `popup_discount`:** A 5.8% conversion looks bad, but popups also have a hidden cost - they annoy users and increase bounce on first sessions. Make sure you're measuring the second-order impact, not just signups.

## Recommended Strategies

1. **Add the newsletter signup to a higher-attention surface** - inline mid-article on blog posts, or a slim banner on product pages. Footer should stay but won't carry the load.
2. **Double down on `{best_id}`** - it's high-intent, high-converting, and probably under-trafficked. Add a clear CTA to it from your About page and product pages where wholesale makes sense.
3. **Reduce friction on `product_question`** - at 12%, there's clearly demand but something on the form itself is dropping people. Likely too many fields. Strip to name + email + question.
4. **A/B test popup_discount timing** - try exit-intent only vs. on-load. Exit-intent typically wins on both conversion AND retention.
"""
    },
]


def render_first_half_split(rows):
    half = len(rows) // 2
    first = sum(r["page_views"] for r in rows[:half])
    second = sum(r["page_views"] for r in rows[half:])
    return first, second


def build_tool_payload(name, args):
    return json.loads(tools.run_tool(name, args))


def render_q1(payload):
    data = payload
    rows = data["rows"]
    first_half, second_half = render_first_half_split(rows)
    delta = (second_half - first_half) / first_half * 100
    return EXAMPLES[0]["answer_md"].format(
        total=data["total_page_views"], days=data["days"],
        avg=data["total_page_views"] // data["days"],
        first_half=first_half, second_half=second_half, delta_pct=delta,
    )


def render_q2(payload):
    f = payload["funnel"]
    return EXAMPLES[1]["answer_md"].format(
        s1=f[0]["users"], s2=f[1]["users"], s3=f[2]["users"],
        s4=f[3]["users"], s5=f[4]["users"], s6=f[5]["users"],
        c2=f[1]["conversion_from_previous_pct"], d2=f[1]["drop_off_from_previous_pct"],
        c3=f[2]["conversion_from_previous_pct"], d3=f[2]["drop_off_from_previous_pct"],
        c4=f[3]["conversion_from_previous_pct"], d4=f[3]["drop_off_from_previous_pct"],
        c5=f[4]["conversion_from_previous_pct"], d5=f[4]["drop_off_from_previous_pct"],
        c6=f[5]["conversion_from_previous_pct"], d6=f[5]["drop_off_from_previous_pct"],
    )


def render_q3(payload):
    rows = payload["rows"]
    table_rows = []
    for i, r in enumerate(rows, 1):
        table_rows.append(
            f"| {i} | {r['product_name']} | {r['item_views']:,} | {r['add_to_carts']:,} | "
            f"{r['purchases']:,} | ${r['revenue']:,.0f} | {r['view_to_cart_rate_pct']}% | "
            f"{r['cart_to_purchase_rate_pct']}% |"
        )
    by_revenue = max(rows, key=lambda r: r["revenue"])
    by_conv = max(rows, key=lambda r: r["view_to_cart_rate_pct"])
    return EXAMPLES[2]["answer_md"].format(
        rows="\n".join(table_rows),
        top_name=rows[0]["product_name"], top_views=rows[0]["item_views"],
        rev_name=by_revenue["product_name"], rev_amt=by_revenue["revenue"],
        best_conv_name=by_conv["product_name"], best_conv_rate=by_conv["view_to_cart_rate_pct"],
    )


def render_q4(payload):
    rows = payload["rows"]
    best = payload["best_form"]
    worst = payload["worst_form"]
    table_rows = []
    for r in rows:
        table_rows.append(
            f"| `{r['form_id']}` | {r['form_location']} | {r['form_type']} | "
            f"{r['form_views']:,} | {r['form_submissions']:,} | **{r['conversion_rate_pct']}%** |"
        )
    return EXAMPLES[3]["answer_md"].format(
        rows="\n".join(table_rows),
        best_id=best["form_id"], best_rate=best["conversion_rate_pct"],
        best_subs=best["form_submissions"], best_views=best["form_views"],
        worst_id=worst["form_id"], worst_rate=worst["conversion_rate_pct"],
        worst_subs=worst["form_submissions"], worst_views=worst["form_views"],
        spread=best["conversion_rate_pct"] / worst["conversion_rate_pct"],
    )


RENDERERS = [render_q1, render_q2, render_q3, render_q4]


def md_to_html(md):
    """Minimal markdown -> HTML for the preview."""
    import re
    lines = md.split("\n")
    out = []
    in_table = False
    in_list = False
    for line in lines:
        if line.startswith("## "):
            if in_list:
                out.append("</ol>"); in_list = False
            out.append(f"<h3>{html.escape(line[3:])}</h3>")
        elif line.startswith("| "):
            if not in_table:
                in_table = True
                out.append("<table>")
            cells = [c.strip() for c in line.strip("|").split("|")]
            # detect separator row
            if all(set(c.replace(":", "").replace("-", "")) <= {" ", ""} for c in cells):
                continue
            tag = "th" if not any("table" in o for o in out[-3:]) and out[-1] == "<table>" else "td"
            tag = "th" if out[-1] == "<table>" else "td"
            row = "".join(f"<{tag}>{render_inline(c)}</{tag}>" for c in cells)
            out.append(f"<tr>{row}</tr>")
        elif re.match(r"^\d+\. ", line):
            if not in_list:
                in_list = True
                out.append("<ol>")
            out.append(f"<li>{render_inline(line.split('. ', 1)[1])}</li>")
        elif line.startswith("- "):
            if not in_list:
                in_list = True
                out.append("<ul>")
            out.append(f"<li>{render_inline(line[2:])}</li>")
        elif line.strip() == "":
            if in_table:
                out.append("</table>"); in_table = False
            if in_list:
                out.append("</ol>" if "<ol>" in out[-100:] else "</ul>"); in_list = False
            out.append("")
        else:
            if in_table:
                out.append("</table>"); in_table = False
            if in_list:
                out.append("</ol>" if "<ol>" in out[-100:] else "</ul>"); in_list = False
            out.append(f"<p>{render_inline(line)}</p>")
    if in_table:
        out.append("</table>")
    if in_list:
        out.append("</ol>")
    return "\n".join(out)


def render_inline(s):
    import re
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    return s


# Build the full conversation HTML
conv_html_parts = []
for ex, renderer in zip(EXAMPLES, RENDERERS):
    payload = build_tool_payload(*ex["tool_calls"][0])
    answer_md = renderer(payload)
    answer_html = md_to_html(answer_md)

    # tool call summary
    tool_summary = ", ".join(f"<code>{c[0]}</code>" for c in ex["tool_calls"])

    conv_html_parts.append(f"""
<div class="msg user">
  <div class="avatar">U</div>
  <div class="bubble">{html.escape(ex['q'])}</div>
</div>
<div class="msg assistant">
  <div class="avatar">AI</div>
  <div class="bubble">
    <details class="tool-call">
      <summary>Called {tool_summary}</summary>
      <pre>{html.escape(json.dumps(payload, indent=2)[:600])}{'...' if len(json.dumps(payload)) > 600 else ''}</pre>
    </details>
    {answer_html}
  </div>
</div>
""")

HTML = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>AI Data Scientist - Preview</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: #fafafa; color: #1a1a1a; }}
.layout {{ display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }}
.sidebar {{ background: #f0f2f6; padding: 20px; border-right: 1px solid #e0e0e0; }}
.sidebar h1 {{ font-size: 18px; margin: 0 0 4px; }}
.sidebar p.caption {{ color: #666; font-size: 13px; margin: 0 0 20px; }}
.sidebar h3 {{ font-size: 13px; text-transform: uppercase; color: #555; margin: 20px 0 8px; letter-spacing: 0.5px; }}
.sidebar .status {{ background: #d4edda; color: #155724; padding: 8px 12px; border-radius: 6px; font-size: 13px; }}
.sidebar .example {{ background: white; border: 1px solid #ddd; padding: 8px 12px; border-radius: 6px;
  font-size: 13px; margin-bottom: 6px; cursor: pointer; }}
.main {{ padding: 32px 48px; max-width: 920px; }}
.main > h2 {{ font-size: 22px; margin: 0 0 24px; color: #333; }}
.msg {{ display: flex; gap: 12px; margin-bottom: 24px; }}
.avatar {{ width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-weight: 600; font-size: 13px; color: white; }}
.msg.user .avatar {{ background: #555; }}
.msg.assistant .avatar {{ background: #d97757; }}
.bubble {{ flex: 1; background: white; border: 1px solid #e8e8e8; border-radius: 10px;
  padding: 16px 20px; line-height: 1.55; }}
.msg.user .bubble {{ background: #f0f4f8; border-color: #d0dae5; }}
.bubble h3 {{ font-size: 16px; margin: 18px 0 8px; color: #1a1a1a; border-bottom: 2px solid #f0f0f0;
  padding-bottom: 6px; }}
.bubble h3:first-child {{ margin-top: 0; }}
.bubble p {{ margin: 8px 0; }}
.bubble ol, .bubble ul {{ padding-left: 20px; margin: 8px 0; }}
.bubble li {{ margin: 4px 0; }}
.bubble table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
.bubble th, .bubble td {{ padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }}
.bubble th {{ background: #fafafa; font-weight: 600; color: #555; }}
.bubble td:last-child, .bubble th:last-child {{ text-align: right; font-variant-numeric: tabular-nums; }}
.bubble code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 12px;
  font-family: "SF Mono", Menlo, monospace; }}
.tool-call {{ background: #f8f8f8; border: 1px solid #e8e8e8; border-radius: 6px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 13px; }}
.tool-call summary {{ cursor: pointer; color: #666; }}
.tool-call pre {{ background: #2d2d2d; color: #d4d4d4; padding: 12px; border-radius: 4px;
  font-size: 11px; overflow-x: auto; margin: 8px 0 0; max-height: 200px; }}
.chat-input {{ position: sticky; bottom: 0; background: white; border-top: 1px solid #e0e0e0;
  padding: 16px; border-radius: 8px; box-shadow: 0 -2px 8px rgba(0,0,0,0.04); margin-top: 24px; color: #999; }}
</style>
</head><body>
<div class="layout">
  <aside class="sidebar">
    <h1>AI Data Scientist</h1>
    <p class="caption">Chat-based BI over your Google Analytics data.</p>

    <h3>Model</h3>
    <select style="width:100%; padding:6px; border:1px solid #ccc; border-radius:4px; font-size:13px;">
      <option>claude-opus-4-8</option>
    </select>

    <h3>Data sources</h3>
    <p class="caption" style="margin-top:0;">Demo range: 2026-03-01 to 2026-06-04</p>
    <details style="background:white;border:1px solid #ddd;padding:8px 12px;border-radius:6px;margin-bottom:6px;font-size:13px;">
      <summary style="cursor:pointer;color:#155724;font-weight:500;">Google Analytics 4  —  connected (mock)</summary>
      <div style="margin-top:8px;color:#666;font-size:12px;">Enable GA4 Data API, create service account, set GA4_PROPERTY_ID + service account JSON in secrets.</div>
    </details>
    <details style="background:white;border:1px solid #ddd;padding:8px 12px;border-radius:6px;margin-bottom:6px;font-size:13px;">
      <summary style="cursor:pointer;color:#155724;font-weight:500;">Google Ads  —  connected (mock)</summary>
      <div style="margin-top:8px;color:#666;font-size:12px;">Get developer token, OAuth2 client, refresh token. Set GOOGLE_ADS_* secrets.</div>
    </details>
    <details style="background:white;border:1px solid #ddd;padding:8px 12px;border-radius:6px;margin-bottom:6px;font-size:13px;">
      <summary style="cursor:pointer;color:#155724;font-weight:500;">Meta Ads (FB + IG)  —  connected (mock)</summary>
      <div style="margin-top:8px;color:#666;font-size:12px;">Create Business app, Marketing API, long-lived token. Set META_ACCESS_TOKEN + META_AD_ACCOUNT_ID.</div>
    </details>

    <h3>Try asking</h3>
    <div class="example">How many page views did I get in May 2026?</div>
    <div class="example">Analyse my user journey and tell me where there's a drop-off.</div>
    <div class="example">Which Google Ads campaigns are wasting spend?</div>
    <div class="example">Which Meta Ads creatives are working and which are fatigued?</div>
    <div class="example">Compare Google Ads vs Meta Ads - where should I shift budget?</div>
  </aside>
  <main class="main">
    <h2>Chat preview</h2>
    {''.join(conv_html_parts)}
    <div class="chat-input">Ask about your analytics...</div>
  </main>
</div>
</body></html>"""

with open("preview.html", "w") as f:
    f.write(HTML)

print(f"Wrote preview.html ({len(HTML):,} chars)")
