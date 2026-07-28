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
            page.wait_for_timeout(_render_settle_ms())  # let JS inject popups/lazy content
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
