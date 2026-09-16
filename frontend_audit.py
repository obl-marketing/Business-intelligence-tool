"""Live frontend audit - fetches any page on the user's site and extracts
the structural signals the LLM needs to reason about UX, conversion, and
lead capture: page structure, CTAs, forms, trust signals, content depth.

Combined with GA4 behavioral data, this is what turns "page X has high
bounce" into evidence-backed UX recommendations.
"""
from __future__ import annotations
import os
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


# Heuristics
_CTA_VERBS = re.compile(
    r"\b(buy|shop|add to cart|order|get|start|try|book|reserve|sign up|"
    r"register|subscribe|join|download|request|contact|talk to|schedule|"
    r"learn more|explore|discover|claim|apply|enroll|donate|"
    r"send|submit|continue|next)\b",
    re.IGNORECASE,
)
_TRUST_PATTERNS = {
    "testimonials": re.compile(r"\b(testimonial|customer story|case study|what (our )?customers say)\b", re.I),
    "reviews": re.compile(r"\b(review|star rating|\d+\.\d+ out of 5|verified buyer|\d+ reviews?)\b", re.I),
    "trust_badges": re.compile(r"\b(secure checkout|ssl|verified|guarantee|money[- ]back|trustpilot|bbb)\b", re.I),
    "faq": re.compile(r"\b(faq|frequently asked|common questions)\b", re.I),
    "urgency": re.compile(r"\b(limited time|today only|while supplies last|only \d+ left|hurry|ends \w+|\d+%\s*off)\b", re.I),
    "social_proof_counts": re.compile(r"\b(\d{2,}[\+,]\d{3}|\d+,\d{3}\+?|\d+k\+?)\s+(customers|users|members|signups|downloads|orders|happy)\b", re.I),
    "free_shipping": re.compile(r"\bfree\s+(shipping|delivery|returns)\b", re.I),
}


# Chatbot / live-chat / messaging widget signatures (vendor -> regex).
_CHATBOT_SIGNATURES = {
    "Intercom": r"intercom",
    "Drift": r"drift\.com|driftt",
    "Freshchat": r"freshchat|freshworks|wchat",
    "Zoho SalesIQ": r"salesiq|zohopublic|zoho.*chat",
    "Tawk.to": r"tawk\.to",
    "LiveChat": r"livechatinc|livechat",
    "Crisp": r"crisp\.chat",
    "Tidio": r"tidio",
    "WhatsApp": r"wa\.me|api\.whatsapp|whatsapp",
    "Gupshup": r"gupshup",
    "Haptik": r"haptik",
    "Verloop": r"verloop",
    "Yellow.ai": r"yellow\.ai|yellowmessenger",
    "Engati": r"engati",
    "Kommunicate": r"kommunicate",
    " Meta Messenger": r"facebook\.com/.*customerchat|fb-customerchat",
}


# Cache the sitemap URL list per base URL for the process lifetime - the
# sitemap rarely changes within a session and re-fetching it on every
# resolve/list call is slow.
_SITEMAP_CACHE: dict[str, list[str]] = {}


def _fetch_sitemap_urls(base: str) -> list[str]:
    """Fetch + flatten the site's sitemap(s) into a de-duplicated URL list.
    Cached per base URL. Returns [] if no sitemap is reachable."""
    if base in _SITEMAP_CACHE:
        return _SITEMAP_CACHE[base]

    import xml.etree.ElementTree as ET

    headers = {"User-Agent": _UA, "Accept": "application/xml,text/xml,*/*",
               **_allowlist_headers()}

    def _get(u: str) -> str | None:
        for verify in (True, False):
            try:
                with httpx.Client(follow_redirects=True, timeout=15.0,
                                  headers=headers, verify=verify) as c:
                    r = c.get(u)
                return r.text if r.status_code == 200 else None
            except httpx.HTTPError as e:
                if verify and any(k in str(e).lower() for k in ("ssl", "certificate")):
                    continue
                return None
        return None

    def _locs(xml_text: str) -> list[str]:
        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
        except Exception:
            return []
        return [el.text.strip() for el in root.iter()
                if el.tag.endswith("loc") and el.text]

    page_urls: list[str] = []
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"):
        text = _get(base + path)
        if not text:
            continue
        found = _locs(text)
        children = [u for u in found if u.lower().endswith(".xml")]
        page_urls += [u for u in found if not u.lower().endswith(".xml")]
        for sm in children[:50]:  # cap child sitemaps
            t2 = _get(sm)
            if t2:
                page_urls += [u for u in _locs(t2) if not u.lower().endswith(".xml")]
        if page_urls:
            break

    seen, urls = set(), []
    for u in page_urls:
        if u not in seen:
            seen.add(u)
            urls.append(u)
    _SITEMAP_CACHE[base] = urls
    return urls


def _classify_url(u: str) -> str:
    """Best-effort page-type label from the URL shape.

    On this site PLP / category pages live under '/tiles/' (e.g.
    '/tiles/floor-tiles'); product detail pages (PDPs) do NOT contain '/tiles/'
    and are single deep slugs. Blogs/stores/contact are matched by keyword."""
    path = urlparse(u).path.strip("/").lower()
    if not path:
        return "homepage"
    if "tile-collection" in path or "tile-collections" in path:
        return "collection"
    if any(k in path for k in ("blog", "trends", "article", "news")):
        return "blog"
    if any(k in path for k in ("store", "dealer", "contact", "about", "locator")):
        return "info"
    if path.startswith("tiles/") or "/tiles/" in ("/" + path):
        # /tiles/... with one segment after is the category PLP; deeper is a sub-PLP
        segs = path.split("/")
        return "plp" if len(segs) <= 2 else "sub_plp"
    return "pdp"


def resolve_page_url(query: str, limit: int = 8) -> dict:
    """Turn a plain-language page reference ('flexi tiles', 'the floor tile
    category', 'bathroom wall tiles page') into the REAL URL(s) on the site,
    so the user never has to paste a link. Ranks sitemap URLs by how well their
    slug matches the query words, and labels each as a category/PLP page vs a
    product (PDP) vs blog/info.

    Returns candidates with both the full URL and the `page_path` (what GA4 uses
    for pagePath filtering), so the caller can immediately pull GA4 metrics for
    the resolved page and/or audit it - this is how the audit tool and GA4 stay
    in sync for website questions."""
    base = os.environ.get("SITE_BASE_URL", "").rstrip("/")
    if not base:
        return {"error": "SITE_BASE_URL isn't set, so I can't resolve page names to "
                         "URLs. Set it in secrets, or pass a path/URL directly."}

    urls = _fetch_sitemap_urls(base)
    if not urls:
        return {"query": query,
                "note": "Couldn't read the sitemap to resolve that page name. Give me "
                        "the URL or a distinctive part of the path and I'll use it."}

    # Tokenize the query into meaningful words (drop filler like 'page','the').
    stop = {"page", "pages", "the", "my", "for", "of", "on", "a", "an", "and",
            "tile", "tiles", "category", "product", "url", "link", "section"}
    raw_tokens = re.findall(r"[a-z0-9]+", query.lower())
    tokens = [t for t in raw_tokens if t not in stop] or raw_tokens

    scored = []
    for u in urls:
        slug = urlparse(u).path.strip("/").lower()
        slug_words = set(re.findall(r"[a-z0-9]+", slug))
        # score: matched query tokens, weighted; exact whole-phrase match boosts.
        matched = sum(1 for t in tokens if t in slug or t in slug_words)
        if not matched:
            continue
        score = matched / max(len(tokens), 1)
        # Prefer shorter, cleaner paths (a category page over a deep variant).
        depth_penalty = slug.count("/") * 0.03
        # Reward when every token appears.
        if matched == len(tokens):
            score += 0.3
        scored.append((score - depth_penalty, u))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]
    if not top:
        return {"query": query, "tokens_used": tokens,
                "note": f"No sitemap URL matched '{query}'. Try different wording, or "
                        "call list_site_pages to see the site's sections."}

    candidates = []
    for score, u in top:
        candidates.append({
            "url": u,
            "page_path": urlparse(u).path or "/",
            "type": _classify_url(u),
            "match_score": round(min(score, 1.0), 2),
        })
    # Group so the agent can pick the right altitude (category vs product).
    plp = [c for c in candidates if c["type"] in ("plp", "sub_plp")]
    pdp = [c for c in candidates if c["type"] == "pdp"]
    return {
        "query": query,
        "tokens_used": tokens,
        "best_match": candidates[0],
        "category_or_plp_pages": plp,
        "product_pages": pdp[:5],
        "all_candidates": candidates,
        "note": (
            "Use `page_path` with GA4 tools (query_page_metrics page_path_contains, "
            "or exact=true for one row) and/or audit_page(url). For 'demand for X', a "
            "category/PLP page (type plp) reflects category demand; a PDP reflects one "
            "product. If several match, the category/PLP page is usually the right one."
        ),
    }


def extract_filter_links(url_or_path: str, param_contains: str | None = None,
                         name_contains: str | None = None, limit: int = 60) -> dict:
    """Render a listing/PLP page and read its FILTER options, mapping each human
    label (e.g. 'Inspire XL') to the real filter URL + query value.

    This is how STARS handles filters/collections whose URL does NOT contain the
    readable name: the filter link is something like
    `/tiles?tile_collections=1234`, so we render the page (JS included) and pull
    each filter anchor's visible text + href. That turns "Inspire XL" into the
    exact `?tile_collections=<value>` the site (and GA4's pagePath) uses.

    - `param_contains`: only keep filters whose query key contains this (e.g.
      'tile_collection' for collections, 'color', 'size'). Omit to see all filters.
    - `name_contains`: only keep options whose label contains this (e.g. 'inspire').
    Returns each option with `url`, `page_path`, `param`, `value`, and
    `ga4_page_path_contains` (the distinctive `param=value` substring to filter GA4
    pagePath by)."""
    from urllib.parse import urlparse, urljoin, parse_qsl

    url = _resolve_url(url_or_path)
    fetched = _fetch(url)
    if fetched.get("error"):
        return {"url": url, **fetched}

    final_url = fetched["final_url"]
    soup = BeautifulSoup(fetched["html"], "lxml")

    seen: set[tuple] = set()
    options: list[dict] = []
    params_seen: dict[str, int] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(final_url, href)
        query = urlparse(absolute).query
        if not query:
            continue
        pairs = parse_qsl(query, keep_blank_values=False)
        if not pairs:
            continue
        label = (a.get_text(strip=True) or a.get("aria-label") or "").strip()
        if not label or len(label) > 60:
            continue
        for key, value in pairs:
            params_seen[key] = params_seen.get(key, 0) + 1
            if param_contains and param_contains.lower() not in key.lower():
                continue
            if name_contains and name_contains.lower() not in label.lower():
                continue
            path = urlparse(absolute).path or "/"
            dedup = (label.lower(), key, value)
            if dedup in seen:
                continue
            seen.add(dedup)
            options.append({
                "label": label,
                "param": key,
                "value": value,
                "url": absolute,
                "page_path": f"{path}?{key}={value}",
                "ga4_page_path_contains": f"{key}={value}",
            })
            if len(options) >= limit:
                break
        if len(options) >= limit:
            break

    note = None
    if not options:
        if param_contains:
            note = (f"No filter links matching '{param_contains}' were found on this "
                    f"page. The filters may be rendered client-side without <a href> "
                    f"links, or the param name differs - the params seen here were: "
                    f"{sorted(params_seen)[:20]}. Try a different listing page or omit "
                    "param_contains to list everything.")
        else:
            note = ("No querystring filter links found on this page. Try a category/PLP "
                    "page that has a filter sidebar (e.g. /tiles/floor-tiles).")
    return {
        "url": url,
        "final_url": final_url,
        "render_mode": fetched.get("render_mode"),
        "param_contains": param_contains,
        "name_contains": name_contains,
        "params_available": dict(sorted(params_seen.items(), key=lambda kv: kv[1], reverse=True)[:25]),
        "filters": options,
        "filter_count": len(options),
        "note": note,
    }


def list_site_pages(limit: int = 150) -> dict:
    """Discover the site's pages from its sitemap so an audit can span the whole
    site. Returns a URL sample plus a count of pages per top-level section, so the
    agent can pick one of each key page type (homepage, PLP, product, contact,
    blog) to audit."""
    from urllib.parse import urlparse

    base = os.environ.get("SITE_BASE_URL", "").rstrip("/")
    if not base:
        return {"error": "SITE_BASE_URL isn't set, so I can't locate the sitemap. "
                         "Set it in secrets, or give me the specific page URLs to audit."}

    urls = _fetch_sitemap_urls(base)
    if not urls:
        return {"note": "Couldn't read a sitemap at the usual paths. Give me the key "
                        "page URLs to audit (homepage, a category page, a product page, "
                        "contact, a blog post) and I'll audit those directly."}

    sections: dict[str, int] = {}
    for u in urls:
        seg = urlparse(u).path.strip("/").split("/")[0] or "(homepage)"
        sections[seg] = sections.get(seg, 0) + 1

    return {
        "total_pages": len(urls),
        "sections": dict(sorted(sections.items(), key=lambda kv: kv[1], reverse=True)[:40]),
        "sample_urls": urls[:limit],
        "note": "To audit site-wide lead touchpoints, pick ONE representative page per "
                "key section (homepage, a PLP/category, a product page, contact, a blog "
                "post) and audit each - popups/chatbot are usually site-wide, forms vary "
                "by page type.",
    }


def _detect_chatbot(html: str, soup: BeautifulSoup) -> dict:
    """Detect a chatbot / live-chat / messaging widget on the page: named vendors
    by signature, plus a generic fallback for custom widgets."""
    low = (html or "").lower()
    vendors = [name for name, pat in _CHATBOT_SIGNATURES.items()
               if re.search(pat, low)]
    generic = bool(soup.select(
        "[id*=chatbot],[class*=chatbot],[id*=chat-widget],[class*=chat-widget],"
        "[class*=livechat],[id*=livechat],iframe[src*=chat],iframe[title*=chat],"
        "[aria-label*=chat],[class*=widget-chat]"
    ))
    return {
        "detected": bool(vendors) or generic,
        "vendors_identified": vendors,
        "custom_widget_signals": generic and not vendors,
        "note": ("Named vendor(s) detected." if vendors else
                 "A chat-like widget was detected but not tied to a known vendor - "
                 "likely a custom/in-house chatbot." if generic else
                 "No chatbot/live-chat widget detected on this page."),
    }


def _resolve_url(target: str) -> str:
    """Accept full URL or path. Path uses SITE_BASE_URL secret if available."""
    if target.startswith(("http://", "https://")):
        return target
    base = os.environ.get("SITE_BASE_URL", "").rstrip("/")
    if not base:
        raise ValueError(
            "Got a path but SITE_BASE_URL is not set. Add SITE_BASE_URL "
            "to Streamlit secrets (e.g. 'https://www.yoursite.com') or "
            "pass the full URL."
        )
    return base + ("/" + target.lstrip("/"))


def _classify_form(form_tag) -> dict[str, Any]:
    inputs = form_tag.find_all(["input", "textarea", "select"])
    visible_fields = []
    hidden_fields = 0
    required_fields = 0
    for el in inputs:
        ftype = (el.get("type") or el.name or "").lower()
        if ftype in ("hidden", "submit", "button"):
            if ftype == "hidden":
                hidden_fields += 1
            continue
        is_required = el.has_attr("required")
        if is_required:
            required_fields += 1
        visible_fields.append({
            "name": el.get("name") or el.get("id") or "(unnamed)",
            "type": ftype,
            "required": is_required,
            "placeholder": (el.get("placeholder") or "").strip()[:60],
        })
    submit_label = ""
    submit_el = form_tag.find(
        lambda t: t.name in ("button", "input")
        and (t.get("type") or "submit").lower() in ("submit", "button")
    )
    if submit_el:
        submit_label = (submit_el.get_text(strip=True) or submit_el.get("value") or "").strip()[:60]

    return {
        "action": form_tag.get("action") or "(self)",
        "method": (form_tag.get("method") or "GET").upper(),
        "visible_field_count": len(visible_fields),
        "required_field_count": required_fields,
        "hidden_field_count": hidden_fields,
        "submit_label": submit_label,
        "fields": visible_fields[:20],  # cap for token budget
        "id": form_tag.get("id") or form_tag.get("name") or "",
    }


def _extract_ctas(soup: BeautifulSoup) -> list[dict]:
    ctas = []
    for el in soup.find_all(["a", "button"]):
        text = (el.get_text(strip=True) or el.get("aria-label") or "").strip()
        if not text or len(text) > 60:
            continue
        if not _CTA_VERBS.search(text):
            continue
        ctas.append({
            "text": text,
            "element": el.name,
            "href": el.get("href", "") if el.name == "a" else "",
            "classes": " ".join(el.get("class", []))[:80],
        })
        if len(ctas) >= 25:
            break
    return ctas


def _text_only(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


# A realistic desktop-Chrome UA. WAFs/CDNs (Cloudflare, Akamai) 403 obvious bot
# UAs, and headless Chromium's native UA contains "HeadlessChrome" which is also
# flagged - so both fetch paths present this instead.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
_VIEWPORT_H = 900


def _render_mode() -> str:
    """AUDIT_JS_RENDER: 'auto' (default) tries a headless browser then falls back
    to static HTML; 'on' forces the browser; 'off' uses static HTML only."""
    return (os.environ.get("AUDIT_JS_RENDER", "auto") or "auto").strip().lower()


def _allowlist_headers() -> dict:
    """If AUDIT_ALLOWLIST_TOKEN is set, every audit request carries
    `X-STARS-Audit: <token>`. Allowlist that header at your WAF to let the
    audit through without touching real-visitor traffic. Works regardless of
    the server's IP (useful when the egress IP isn't static)."""
    tok = (os.environ.get("AUDIT_ALLOWLIST_TOKEN") or "").strip()
    return {"X-STARS-Audit": tok} if tok else {}


def _render_settle_ms() -> int:
    """How long to wait after load for client-side JS to inject popups / lazy
    content. Configurable via AUDIT_RENDER_WAIT_MS (raise it to catch delayed
    popups, e.g. a 15-second exit-intent). Default 3000ms, capped at 30000."""
    try:
        return max(0, min(int(os.environ.get("AUDIT_RENDER_WAIT_MS", "3000")), 30000))
    except ValueError:
        return 3000


_POPUP_DETECT_JS = """() => {
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 60 && r.height > 60 && s.visibility !== 'hidden'
        && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.1;
  };
  const sel = '[role=dialog],[aria-modal=true],.modal,.popup,.mfp-content,'
    + '.fancybox-container,[class*=popup],[class*=Popup],[class*=modal],'
    + '[class*=Modal],[id*=popup],[id*=modal]';
  const out = [];
  document.querySelectorAll(sel).forEach(el => {
    if (!vis(el)) return;
    const s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'absolute') return;
    out.push({
      id: el.id || '',
      cls: (el.className && el.className.toString ? el.className.toString() : '').slice(0, 80),
      text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
      z: parseInt(s.zIndex) || 0,
    });
  });
  return out;
}"""


def _observe_popups(page, window_ms: int) -> list[dict]:
    """Poll the page over `window_ms` and record, for each popup/modal, HOW LONG
    after load it first became visible - i.e. its time-delay trigger. Popups
    present at ~0s load immediately; one at ~15s has a 15-second delay. (Scroll
    and exit-intent popups aren't simulated, so they won't appear here.)"""
    import time
    seen: dict[str, dict] = {}
    start = time.time()
    end = start + max(0, window_ms) / 1000.0
    while True:
        try:
            cands = page.evaluate(_POPUP_DETECT_JS)
        except Exception:
            cands = []
        elapsed = round(time.time() - start, 1)
        for c in cands:
            key = f"{c.get('id','')}|{(c.get('text','') or '')[:40]}|{(c.get('cls','') or '')[:40]}"
            if key.strip("|") and key not in seen:
                seen[key] = {
                    "first_seen_seconds": elapsed,
                    "id": c.get("id", ""),
                    "class": c.get("cls", ""),
                    "text": c.get("text", ""),
                    "z_index": c.get("z", 0),
                }
        if time.time() >= end:
            break
        page.wait_for_timeout(700)
    return sorted(seen.values(), key=lambda p: p["first_seen_seconds"])[:20]


def _render_html_playwright(url: str, timeout_ms: int = 30000) -> dict:
    """Load the page in headless Chromium so JavaScript runs, and return the
    fully-rendered HTML plus each clickable element's viewport position (for
    real above-the-fold detection). Raises if Playwright/Chromium isn't set up.

    Set PLAYWRIGHT_CHROMIUM_PATH to use a system-installed Chromium (e.g. one
    from apt) instead of Playwright's own download - useful when a firewall
    blocks Playwright's browser CDN.
    """
    from playwright.sync_api import sync_playwright

    launch_kwargs: dict = {
        "headless": True,
        "args": [
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            # Hide the automation flag that basic bot-detection checks for.
            "--disable-blink-features=AutomationControlled",
        ],
    }
    exe = (os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or "").strip()
    if exe:
        launch_kwargs["executable_path"] = exe

    out: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        try:
            ctx = browser.new_context(
                user_agent=_UA,
                viewport={"width": 1366, "height": _VIEWPORT_H},
                locale="en-US",
                timezone_id="Asia/Kolkata",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9",
                                    **_allowlist_headers()},
                # Auditing the user's own public pages; don't let an incomplete
                # cert chain (which real browsers tolerate via AIA) block the read.
                ignore_https_errors=True,
            )
            # Stealth: mask the headless/automation fingerprints that WAF bot
            # detection looks for, so auditing our own site isn't blocked.
            ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});"
                "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
                "window.chrome={runtime:{}};"
            )
            page = ctx.new_page()
            # 'domcontentloaded' is reliable; 'networkidle' never fires on sites
            # with chat/analytics/ad pixels that poll continuously, so it would
            # just time out. If navigation is slow we still grab what rendered.
            resp = None
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
            # Actively watch over the settle window and timestamp each popup as it
            # appears (instead of a passive wait) - this yields the trigger timing.
            settle = _render_settle_ms()
            out["popups"] = _observe_popups(page, settle)
            out["popup_watch_seconds"] = round(settle / 1000.0, 1)
            try:
                page.wait_for_load_state("load", timeout=6000)
            except Exception:
                pass
            out["html"] = page.content()
            out["status"] = resp.status if resp else None
            out["final_url"] = page.url
            try:
                out["elements"] = page.eval_on_selector_all(
                    "a, button",
                    """els => els.map(e => {
                        const r = e.getBoundingClientRect();
                        return {
                            text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0,60),
                            top: Math.round(r.top),
                            visible: r.width > 0 && r.height > 0 &&
                                     getComputedStyle(e).visibility !== 'hidden'
                        };
                    })""",
                )
            except Exception:
                out["elements"] = None
            return out
        finally:
            browser.close()


def _render_in_thread(url: str) -> dict:
    """Run sync Playwright off the Streamlit thread to avoid any event-loop clash."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_render_html_playwright, url).result(timeout=90)


def _fetch(url: str) -> dict:
    """Fetch page HTML, rendering JavaScript when possible. Returns a dict with
    html/status/final_url/page_size_kb/elapsed_ms/render_mode (or 'error')."""
    mode = _render_mode()
    started = time.time()
    render_note = None

    if mode in ("auto", "on"):
        js_result = None
        try:
            r = _render_in_thread(url)
            html = r.get("html") or ""
            js_result = {
                "html": html,
                "status": r.get("status"),
                "final_url": r.get("final_url") or url,
                "page_size_kb": round(len(html.encode("utf-8")) / 1024, 1),
                "elapsed_ms": int((time.time() - started) * 1000),
                "render_mode": "javascript (headless Chromium)",
                "elements": r.get("elements"),
                "popups": r.get("popups"),
                "popup_watch_seconds": r.get("popup_watch_seconds"),
            }
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            if mode == "on":
                return {"error": (
                    f"JS render failed: {reason}. Install the browser on "
                    "the server: `pip install playwright && playwright install --with-deps "
                    "chromium`. Or set AUDIT_JS_RENDER=off to use static HTML.")}
            # auto: fall back to static fetch, but record WHY the browser didn't run
            render_note = (
                "Headless-browser render was attempted but fell back to static HTML. "
                f"Reason: {reason[:300]}. Install/repair it with "
                "`playwright install --with-deps chromium` on the server."
            )
        if js_result is not None:
            blocked = js_result["status"] and js_result["status"] >= 400
            if mode == "auto" and blocked:
                # The site's WAF blocked the automated browser (bot detection),
                # but the plain static request often gets through - retry with it.
                render_note = (
                    f"The headless browser was blocked (HTTP {js_result['status']}), "
                    "likely bot detection; retried with a static fetch. Note: JS-injected "
                    "content (popups, lazy sections) may be missing as a result.")
            else:
                return js_result

    # Full browser-like header set so a WAF/CDN treats us as a real visitor.
    # (Accept-Encoding is intentionally omitted - httpx sets it to only what it
    # can actually decompress, avoiding garbled bodies.)
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        **_allowlist_headers(),
    }
    response = None
    tls_note = None
    # First verify normally; if the cert chain doesn't validate (common when a
    # site serves an incomplete chain that browsers fix via AIA), retry without
    # verification - we're only reading the user's own public HTML.
    for verify in (True, False):
        try:
            with httpx.Client(follow_redirects=True, timeout=15.0,
                              headers=headers, verify=verify) as client:
                response = client.get(url)
            if not verify:
                tls_note = ("TLS certificate verification was skipped: the site's "
                            "certificate chain didn't validate with Python's default "
                            "trust store (real browsers tolerate this). The page content "
                            "is still accurate; if this is unexpected, check the site's "
                            "SSL certificate chain.")
            break
        except httpx.HTTPError as e:
            emsg = f"{type(e).__name__}: {e}"
            if verify and any(k in emsg.lower() for k in ("ssl", "certificate", "verify")):
                continue  # retry with verification disabled
            return {"error": f"Fetch failed: {emsg}"}
    if response is None:
        return {"error": "Fetch failed: could not connect even after an SSL retry.",
                "render_note": render_note}
    return {
        "html": response.text,
        "status": response.status_code,
        "final_url": str(response.url),
        "page_size_kb": round(len(response.content) / 1024, 1),
        "elapsed_ms": int((time.time() - started) * 1000),
        "render_mode": "static HTML (no JavaScript)",
        "tls_note": tls_note,
        "render_note": render_note,
        "elements": None,
    }


def _above_fold_ctas(elements: list | None) -> dict | None:
    """From the browser's element positions, count CTAs actually visible within
    the first viewport (real above-the-fold, not a DOM-order guess)."""
    if not elements:
        return None
    above, below = [], 0
    for el in elements:
        text = (el.get("text") or "").strip()
        if not text or not el.get("visible"):
            continue
        if not _CTA_VERBS.search(text):
            continue
        if 0 <= el.get("top", 99999) < _VIEWPORT_H:
            if text not in above:
                above.append(text)
        else:
            below += 1
    return {"above_fold_cta_count": len(above),
            "above_fold_ctas": above[:15],
            "below_fold_cta_count": below}


def audit_page(url_or_path: str) -> dict[str, Any]:
    url = _resolve_url(url_or_path)

    fetched = _fetch(url)
    if fetched.get("error"):
        return {"url": url, **fetched}

    elapsed_ms = fetched["elapsed_ms"]
    page_size_kb = fetched["page_size_kb"]
    final_url = fetched["final_url"]
    status_code = fetched["status"]
    render_mode = fetched["render_mode"]
    tls_note = fetched.get("tls_note")
    render_note = fetched.get("render_note")
    above_fold = _above_fold_ctas(fetched.get("elements"))
    popups_detected = fetched.get("popups")
    popup_watch_seconds = fetched.get("popup_watch_seconds")

    if status_code is not None and status_code >= 400:
        result = {
            "url": url,
            "final_url": final_url,
            "status": status_code,
            "error": f"HTTP {status_code}",
            "page_size_kb": page_size_kb,
            "render_mode": render_mode,
            "render_note": render_note,
        }
        if status_code in (403, 429) and render_mode.startswith("static"):
            result["hint"] = (
                "A 403/429 on a tiny page is usually the site's WAF/CDN (e.g. "
                "Cloudflare) blocking non-browser requests. Install the headless "
                "browser on the server (`playwright install --with-deps chromium`) - "
                "the real-browser render passes these checks where a plain HTTP fetch "
                "cannot."
            )
        return result

    soup = BeautifulSoup(fetched["html"], "lxml")

    # head
    title = (soup.title.get_text(strip=True) if soup.title else "")[:160]
    meta_desc_el = soup.find("meta", attrs={"name": "description"})
    meta_description = (meta_desc_el.get("content", "") if meta_desc_el else "").strip()[:300]
    viewport_meta = bool(soup.find("meta", attrs={"name": "viewport"}))
    canonical_el = soup.find("link", attrs={"rel": "canonical"})
    canonical = canonical_el.get("href", "") if canonical_el else ""

    # OG / social
    og_title = (soup.find("meta", attrs={"property": "og:title"}) or {}).get("content", "")[:160]
    og_image = bool(soup.find("meta", attrs={"property": "og:image"}))

    # Structure
    h1s = [h.get_text(strip=True)[:140] for h in soup.find_all("h1")][:5]
    h2s = [h.get_text(strip=True)[:140] for h in soup.find_all("h2")][:15]
    h3s = [h.get_text(strip=True)[:120] for h in soup.find_all("h3")][:20]

    # Content
    body_text = _text_only(soup)
    word_count = len(body_text.split())

    # Links
    all_links = soup.find_all("a", href=True)
    parsed_host = urlparse(final_url).netloc
    internal = external = 0
    for a in all_links:
        href = a["href"]
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        absolute = urljoin(final_url, href)
        host = urlparse(absolute).netloc
        if host == parsed_host or not host:
            internal += 1
        else:
            external += 1

    # CTAs
    ctas = _extract_ctas(soup)

    # Forms
    forms = [_classify_form(f) for f in soup.find_all("form")]

    # Images
    imgs = soup.find_all("img")
    images_without_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

    # Trust / conversion signals
    trust_signals = {name: bool(pattern.search(body_text)) for name, pattern in _TRUST_PATTERNS.items()}
    has_schema_org = bool(soup.find("script", attrs={"type": "application/ld+json"}))

    # Form complexity scoring
    form_score = "n/a"
    if forms:
        max_fields = max(f["visible_field_count"] for f in forms)
        if max_fields <= 3:
            form_score = "low (3 or fewer fields - good)"
        elif max_fields <= 6:
            form_score = "medium (4-6 fields - acceptable)"
        else:
            form_score = f"high ({max_fields} fields - friction risk)"

    # Heuristic above-fold signal (no JS render here, so this is rough)
    # Real measurement needs a headless browser; instead we estimate by
    # looking at where in the HTML the first CTA appears.
    first_cta_position = None
    for i, el in enumerate(soup.find_all(["a", "button"])):
        text = (el.get_text(strip=True) or "")
        if text and _CTA_VERBS.search(text):
            first_cta_position = i
            break

    return {
        "url": url,
        "final_url": final_url,
        "status": status_code,
        "render_mode": render_mode,
        "tls_note": tls_note,
        "render_note": render_note,
        "load_time_ms": elapsed_ms,
        "page_size_kb": page_size_kb,

        "title": title,
        "meta_description": meta_description,
        "og_title": og_title,
        "has_og_image": og_image,
        "canonical": canonical,
        "viewport_meta_present": viewport_meta,
        "schema_org_present": has_schema_org,

        "h1": h1s,
        "h2": h2s,
        "h3_sample": h3s,
        "word_count": word_count,
        "content_density_note": (
            "Very thin content (<200 words)" if word_count < 200 else
            "Light content (200-500 words)" if word_count < 500 else
            "Medium content (500-1500 words)" if word_count < 1500 else
            "Long content (1500+ words)"
        ),

        "links": {
            "total": len(all_links),
            "internal": internal,
            "external": external,
        },

        "ctas": {
            "count": len(ctas),
            "first_cta_position_in_dom": first_cta_position,
            # Real viewport above-the-fold data when JS-rendered; None on static fetch.
            "above_the_fold": above_fold,
            "list": ctas,
        },

        "forms": {
            "count": len(forms),
            "complexity": form_score,
            "details": forms,
        },

        "images": {
            "total": len(imgs),
            "missing_alt_text": images_without_alt,
        },

        "trust_and_conversion_signals_detected": trust_signals,

        "chatbot": _detect_chatbot(fetched["html"], soup),

        "popups": {
            "watched_seconds": popup_watch_seconds,
            "detected": popups_detected or [],
            "note": (
                "first_seen_seconds = how many seconds after page load each popup "
                "appeared in a fresh session, i.e. its time-delay trigger (0s = shows "
                "immediately; ~15s = 15-second delay). Only time-triggered popups are "
                "captured; scroll- and exit-intent-triggered popups are NOT simulated so "
                "they won't appear. Watched for 'watched_seconds' total - a popup firing "
                "later than that is missed; raise AUDIT_RENDER_WAIT_MS to watch longer."
                if popups_detected is not None else
                "Popup timing needs the JavaScript (headless Chromium) render; this was a "
                "static fetch."
            ),
        },

        "audit_notes": (
            "Fully JavaScript-rendered in a headless Chromium browser: SPA modals, "
            "lazy-loaded sections, and dynamically-injected content ARE captured, and "
            "above-the-fold data reflects the real 1366x900 viewport."
            if render_mode.startswith("javascript") else
            "Static server-rendered HTML snapshot (JS did not run). JavaScript-injected "
            "content (SPA modals, lazy-loaded sections, A/B variants) is NOT captured, and "
            "above-the-fold is estimated by first-CTA DOM position. To capture JS content, "
            "install the headless browser on the server (see AUDIT_JS_RENDER)."
        ),
    }
