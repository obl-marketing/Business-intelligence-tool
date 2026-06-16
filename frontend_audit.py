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


def audit_page(url_or_path: str) -> dict[str, Any]:
    url = _resolve_url(url_or_path)
    started = time.time()

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BIToolAuditBot/1.0; +https://aistudio.google.com)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
            response = client.get(url)
    except httpx.HTTPError as e:
        return {"url": url, "error": f"Fetch failed: {type(e).__name__}: {e}"}

    elapsed_ms = int((time.time() - started) * 1000)
    page_size_kb = round(len(response.content) / 1024, 1)
    final_url = str(response.url)

    if response.status_code >= 400:
        return {
            "url": url,
            "final_url": final_url,
            "status": response.status_code,
            "error": f"HTTP {response.status_code}",
            "page_size_kb": page_size_kb,
        }

    soup = BeautifulSoup(response.text, "lxml")

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
        "status": response.status_code,
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
            "This is a server-rendered HTML snapshot. JavaScript-injected content "
            "(SPA modals, lazy-loaded sections, A/B variants) is not captured. "
            "Above-the-fold visibility is estimated by first-CTA position in the DOM, "
            "not by actual viewport rendering."
        ),
    }
